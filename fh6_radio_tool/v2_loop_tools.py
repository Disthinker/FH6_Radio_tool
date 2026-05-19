from __future__ import annotations

import json
import math
import shutil
import subprocess
import wave
from array import array
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

from .models import AudioInfo, SegmentMarkers
from .segment_tools import MARKER_ORDER
from .wav_tools import read_wav_info
from .loop_engine.correlation_matcher import analyze_smart_loop_candidates


@dataclass(frozen=True)
class LoopCandidate:
    loop_start: int
    loop_end: int
    score: float
    source: str = "builtin"
    label: str = ""

    def to_json(self) -> dict:
        return asdict(self)


def _parse_ints_from_text(text: str) -> list[int]:
    values: list[int] = []
    buf = ""
    for ch in text:
        if ch.isdigit() or (ch == "-" and not buf):
            buf += ch
        else:
            if buf not in ("", "-"):
                try:
                    values.append(int(buf))
                except Exception:
                    pass
            buf = ""
    if buf not in ("", "-"):
        try:
            values.append(int(buf))
        except Exception:
            pass
    return values


def run_pymusiclooper_candidates(path: Path, top_n: int = 5, timeout_sec: int = 45, progress_callback: Callable[[int, str], None] | None = None) -> list[LoopCandidate]:
    """Try PyMusicLooper if it is installed.

    This wrapper intentionally avoids auto-downloading anything.  If the command
    is absent, callers simply fall back to the built-in lightweight matcher.
    """
    commands: list[list[str]] = []
    if shutil.which("pymusiclooper"):
        commands.append([
            "pymusiclooper", "export-points", "--path", str(path),
            "--export-to", "STDOUT", "--alt-export-top", str(top_n), "--fmt", "SAMPLES",
        ])
    # Also try python -m for users who installed the package without script shim.
    commands.append([
        "python", "-m", "pymusiclooper", "export-points", "--path", str(path),
        "--export-to", "STDOUT", "--alt-export-top", str(top_n), "--fmt", "SAMPLES",
    ])

    candidates: list[LoopCandidate] = []
    seen: set[tuple[int, int]] = set()
    for cmd in commands:
        if progress_callback:
            progress_callback(93, "尝试调用 PyMusicLooper；若未安装会自动跳过，最长等待约 45 秒。")
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout_sec)
        except Exception:
            continue
        if proc.returncode != 0:
            continue
        text = proc.stdout.strip()
        if not text:
            continue
        # PyMusicLooper output has changed across versions.  Parsing all integer
        # pairs is deliberately tolerant; impossible pairs are filtered later.
        ints = _parse_ints_from_text(text)
        for i in range(0, len(ints) - 1, 2):
            start, end = ints[i], ints[i + 1]
            if start < 0 or end <= start:
                continue
            key = (start, end)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(LoopCandidate(start, end, 1.0 - len(candidates) * 0.01, "pymusiclooper", f"PyMusicLooper #{len(candidates)+1}"))
            if len(candidates) >= top_n:
                return candidates
    return candidates


def _read_pcm16_mono_decimated(path: Path, target_points: int = 12000) -> tuple[list[float], AudioInfo]:
    info = read_wav_info(path)
    if info.bits_per_sample != 16:
        raise ValueError(f"内置 loop 分析只支持 16-bit PCM WAV: {path}")
    if info.frames <= 0:
        raise ValueError(f"空 WAV: {path}")

    stride = max(1, info.frames // max(1, target_points))
    points: list[float] = []
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        frame_index = 0
        block_frames = 4096
        while True:
            raw = wf.readframes(block_frames)
            if not raw:
                break
            samples = array("h")
            samples.frombytes(raw)
            if channels <= 0:
                channels = 1
            frame_count = len(samples) // channels
            for fi in range(frame_count):
                if frame_index % stride == 0:
                    s = 0.0
                    base = fi * channels
                    for ch in range(channels):
                        s += float(samples[base + ch])
                    points.append(s / channels / 32768.0)
                frame_index += 1
    # Remove DC bias, lightly compress peaks so waveform texture matters more.
    if points:
        mean = sum(points) / len(points)
        points = [math.tanh((x - mean) * 2.0) for x in points]
    return points, info


def _corr(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n <= 8:
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


def builtin_loop_candidates(
    path: Path,
    top_n: int = 5,
    min_loop_seconds: float = 20.0,
    search_points: int = 12000,
    progress_callback: Callable[[int, str], None] | None = None,
) -> list[LoopCandidate]:
    """A conservative no-dependency loop candidate generator.

    It is not intended to replace PyMusicLooper.  It gives useful starting
    points by comparing short windows near candidate starts and ends.  The user
    must preview and confirm the chosen candidate.
    """
    if progress_callback:
        progress_callback(94, "启动轻量兜底分析。")
    wave_points, info = _read_pcm16_mono_decimated(path, target_points=search_points)
    n = len(wave_points)
    if n < 200:
        return []
    scale = info.frames / n
    requested_min_gap = max(1, int(min_loop_seconds * info.samplerate / scale))
    # For short preview/test files, keep the lower bound proportional so the
    # analyzer can still return candidates.  Real songs will normally keep the
    # 20-second default.
    min_gap = min(requested_min_gap, max(1, int(n * 0.25)))
    win = max(64, min(512, n // 80))

    # Search starts after a short intro and before the middle; ends after middle
    # and before the very end.  This is intentionally broad but bounded.
    start_min = int(n * 0.03)
    start_max = int(n * 0.45)
    end_min = max(int(n * 0.55), start_min + min_gap)
    end_max = int(n * 0.97) - win
    if start_max <= start_min or end_max <= end_min:
        return []

    start_step = max(1, (start_max - start_min) // 55)
    end_step = max(1, (end_max - end_min) // 70)

    scored: list[LoopCandidate] = []
    starts = list(range(start_min, start_max, start_step))
    for si, s in enumerate(starts):
        a = wave_points[s:s + win]
        if len(a) < win:
            continue
        for e in range(max(end_min, s + min_gap), end_max, end_step):
            b = wave_points[e:e + win]
            if len(b) < win:
                continue
            c = _corr(a, b)
            # Prefer long stable loops but avoid choosing almost-full-song tail.
            length_bonus = min(0.08, (e - s) / max(1, n) * 0.08)
            score = c + length_bonus
            if score <= 0.15:
                continue
            start_sample = max(0, min(info.frames - 1, int(round(s * scale))))
            end_sample = max(start_sample + 1, min(info.frames - 1, int(round(e * scale))))
            scored.append(LoopCandidate(start_sample, end_sample, float(score), "builtin", "内置候选"))
        if progress_callback and si % max(1, len(starts) // 10) == 0:
            progress_callback(94 + int(4 * si / max(1, len(starts))), f"轻量兜底分析 {si + 1}/{len(starts)}...")

    # De-duplicate near-identical candidates.
    scored.sort(key=lambda x: x.score, reverse=True)
    out: list[LoopCandidate] = []
    for cand in scored:
        too_close = False
        for prev in out:
            if abs(cand.loop_start - prev.loop_start) < info.samplerate * 2 and abs(cand.loop_end - prev.loop_end) < info.samplerate * 2:
                too_close = True
                break
        if too_close:
            continue
        out.append(cand)
        if len(out) >= top_n:
            break
    return out


def analyze_loop_candidates(path: Path, top_n: int = 5, prefer_pymusiclooper: bool = True, progress_callback: Callable[[int, str], None] | None = None) -> tuple[list[LoopCandidate], str]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    messages: list[str] = []
    candidates: list[LoopCandidate] = []

    # v2.2: run the internal Smart Match engine first.  This is the migrated
    # core workflow inspired by seamless-loop-music: broad candidate search +
    # local reverse/forward correlation refinement.  PyMusicLooper remains an
    # optional external enhancer, not a hard dependency.
    try:
        if progress_callback:
            progress_callback(2, "开始自动分析 Loop 候选；完整歌曲通常约 5–60 秒，取决于时长和 CPU。")
        smart = analyze_smart_loop_candidates(path, top_n=top_n, progress_callback=progress_callback)
        for i, c in enumerate(smart, start=1):
            candidates.append(LoopCandidate(c.loop_start, c.loop_end, c.score, c.source, f"内置 Smart Match #{i}"))
        messages.append(f"内置 Smart Match 返回 {len(smart)} 个候选。")
    except Exception as exc:
        messages.append(f"内置 Smart Match 未能完成：{exc}")

    if len(candidates) < top_n and prefer_pymusiclooper:
        py = run_pymusiclooper_candidates(path, top_n=top_n - len(candidates), progress_callback=progress_callback)
        if py:
            candidates.extend(py)
            messages.append(f"PyMusicLooper 补充返回 {len(py)} 个候选。")
        else:
            messages.append("未检测到可用 PyMusicLooper 或未返回候选。")

    if len(candidates) < top_n:
        light = builtin_loop_candidates(path, top_n=top_n - len(candidates), progress_callback=progress_callback)
        if light:
            candidates.extend(light)
        messages.append(f"内置轻量兜底分析补充返回 {len(light)} 个候选。")

    # De-duplicate mixed sources.
    info = read_wav_info(path)
    out: list[LoopCandidate] = []
    for cand in sorted(candidates, key=lambda c: c.score, reverse=True):
        if any(abs(cand.loop_start - p.loop_start) < info.samplerate * 2 and abs(cand.loop_end - p.loop_end) < info.samplerate * 2 for p in out):
            continue
        out.append(cand)
        if len(out) >= top_n:
            break
    if progress_callback:
        progress_callback(100, f"Loop 候选分析完成：{len(out)} 个候选。")
    return out, "\n".join(messages)


def markers_from_candidate(info: AudioInfo, candidate: LoopCandidate, mode: str = "track") -> SegmentMarkers:
    end_sample = max(0, info.sample_length - 1)
    start = max(0, min(int(candidate.loop_start), end_sample))
    end = max(start, min(int(candidate.loop_end), end_sample))
    pos: dict[str, int] = {"TrackStart": 0, "End": end_sample}
    if mode == "post":
        pos.update({
            "PostDrop": start,
            "PostRaceLoopStart": start,
            "PostRaceLoopEnd": end,
        })
    elif mode == "both":
        pos.update({
            "TrackDrop": start,
            "TrackLoopStart": start,
            "TrackLoopEnd": end,
            "PostDrop": start,
            "PostRaceLoopStart": start,
            "PostRaceLoopEnd": end,
        })
    else:
        pos.update({
            "TrackDrop": start,
            "TrackLoopStart": start,
            "TrackLoopEnd": end,
        })
    return SegmentMarkers({k: v for k, v in pos.items() if k in MARKER_ORDER})
