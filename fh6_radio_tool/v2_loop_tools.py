from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import wave
from array import array
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .models import AudioInfo, SegmentMarkers
from .runtime_tools import is_frozen_app
from .segment_tools import MARKER_ORDER
from .wav_tools import read_wav_info
from .loop_engine.correlation_matcher import analyze_smart_loop_candidates
from .loop_engine.seamless_loopfinder import is_loopfinder_available


@dataclass(frozen=True)
class LoopCandidate:
    loop_start: int
    loop_end: int
    score: float
    source: str = "builtin"
    label: str = ""
    details: dict[str, Any] | None = None

    def to_json(self) -> dict:
        return asdict(self)


NATIVE_LOOPFINDER_SOURCE = "seamless_loopfinder"
NATIVE_LOOPFINDER_MIN_PRIMARY_SCORE = 0.60

_SOURCE_PRIORITY = {
    NATIVE_LOOPFINDER_SOURCE: 0,
    "pymusiclooper": 1,
    "internal_smart_match_fast": 2,
    "builtin": 3,
}


def _source_priority(source: str) -> int:
    return _SOURCE_PRIORITY.get(str(source), 10)


def _candidate_rank_key(candidate: LoopCandidate) -> tuple[int, float]:
    priority = _source_priority(candidate.source)
    if candidate.source == NATIVE_LOOPFINDER_SOURCE and float(candidate.score) < NATIVE_LOOPFINDER_MIN_PRIMARY_SCORE:
        priority = 4
    return priority, -float(candidate.score)


def _loopfinder_worker_command() -> list[str]:
    if is_frozen_app():
        return [sys.executable, "--loopfinder-worker"]
    return [sys.executable, "-m", "fh6_radio_tool.loopfinder_worker"]


def _candidates_from_loopfinder_payload(payload: dict[str, Any]) -> list[LoopCandidate]:
    out: list[LoopCandidate] = []
    for i, raw in enumerate(payload.get("candidates") or [], start=1):
        if not isinstance(raw, dict):
            continue
        try:
            start = int(raw.get("loop_start", raw.get("loopStart")))
            end = int(raw.get("loop_end", raw.get("loopEnd")))
            score = float(raw.get("score", 0.0))
        except Exception:
            continue
        if start < 0 or end <= start:
            continue
        note_diff = raw.get("note_diff", raw.get("noteDiff"))
        loudness_diff = raw.get("loudness_diff", raw.get("loudnessDiff"))
        details = {
            "dll": payload.get("dll", ""),
            "elapsed_sec": payload.get("elapsed_sec"),
            "note_diff": note_diff,
            "loudness_diff": loudness_diff,
            "input": payload.get("input", ""),
            "used_temp_input": payload.get("used_temp_input", False),
        }
        out.append(LoopCandidate(
            start,
            end,
            score,
            NATIVE_LOOPFINDER_SOURCE,
            f"Seamless LoopFinder #{i}",
            details,
        ))
    return out


def _run_seamless_loopfinder_analysis(path: Path, top_n: int = 8, timeout_sec: int = 180) -> tuple[list[LoopCandidate], str]:
    ok, detail = is_loopfinder_available()
    if not ok:
        raise FileNotFoundError(detail)

    cmd = _loopfinder_worker_command() + ["--input", str(path), "--top-n", str(top_n), "--dll", detail]
    proc = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_sec,
        creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0),
    )
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    try:
        payload = json.loads(stdout) if stdout else {}
    except Exception as exc:
        raise RuntimeError(f"LoopFinder worker returned invalid JSON: {exc}; stderr={stderr[:400]}") from exc
    if proc.returncode != 0 or not payload.get("ok"):
        err = payload.get("error") or stderr or f"worker exit {proc.returncode}"
        raise RuntimeError(str(err))
    candidates = _candidates_from_loopfinder_payload(payload)
    best = max((c.score for c in candidates), default=0.0)
    msg = (
        f"Seamless LoopFinder returned {len(candidates)} candidate(s); "
        f"dll={payload.get('dll')}; elapsed={payload.get('elapsed_sec')}s; best={best:.4f}."
    )
    if candidates:
        first = candidates[0]
        if first.details:
            msg += (
                f" note_diff={first.details.get('note_diff')}; "
                f"loudness_diff={first.details.get('loudness_diff')}."
            )
    if stderr:
        msg += f"\nLoopFinder stderr: {stderr[-800:]}"
    return candidates, msg


def run_seamless_loopfinder_candidates(path: Path, top_n: int = 8, timeout_sec: int = 180) -> list[LoopCandidate]:
    candidates, _ = _run_seamless_loopfinder_analysis(path, top_n=top_n, timeout_sec=timeout_sec)
    return candidates


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


def _dedupe_and_rank_candidates(candidates: list[LoopCandidate], info: AudioInfo, top_n: int) -> list[LoopCandidate]:
    out: list[LoopCandidate] = []
    for cand in sorted(candidates, key=_candidate_rank_key):
        if any(abs(cand.loop_start - p.loop_start) < info.samplerate * 2 and abs(cand.loop_end - p.loop_end) < info.samplerate * 2 for p in out):
            continue
        out.append(cand)
        if len(out) >= top_n:
            break
    return out


def analyze_loop_candidates(
    path: Path,
    top_n: int = 5,
    prefer_pymusiclooper: bool = True,
    prefer_loopfinder: bool = True,
    progress_callback: Callable[[int, str], None] | None = None,
) -> tuple[list[LoopCandidate], str]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    messages: list[str] = []
    candidates: list[LoopCandidate] = []
    native_primary = False

    if prefer_loopfinder:
        if progress_callback:
            progress_callback(2, "启动 Seamless LoopFinder 原生分析；失败时会自动回退。")
        try:
            native, msg = _run_seamless_loopfinder_analysis(path, top_n=top_n)
            candidates.extend(native)
            messages.append(msg)
            best_native = max((c.score for c in native), default=0.0)
            native_primary = bool(native and best_native >= NATIVE_LOOPFINDER_MIN_PRIMARY_SCORE)
            if native_primary:
                messages.append(f"Seamless LoopFinder score {best_native:.4f} accepted as primary; fallback skipped.")
            elif native:
                messages.append(f"Seamless LoopFinder best score {best_native:.4f} is below {NATIVE_LOOPFINDER_MIN_PRIMARY_SCORE:.2f}; fallback enabled.")
            else:
                messages.append("Seamless LoopFinder returned no candidates; fallback enabled.")
        except Exception as exc:
            messages.append(f"Seamless LoopFinder unavailable or failed: {exc}")

    if not native_primary:
        if len(candidates) < top_n and prefer_pymusiclooper:
            py = run_pymusiclooper_candidates(path, top_n=top_n - len(candidates), progress_callback=progress_callback)
            if py:
                candidates.extend(py)
                messages.append(f"PyMusicLooper fallback returned {len(py)} candidate(s).")
            else:
                messages.append("PyMusicLooper unavailable or returned no candidates.")

        if len(candidates) < top_n:
            try:
                if progress_callback:
                    progress_callback(35, "启动内置 Smart Match 回退分析。")
                smart = analyze_smart_loop_candidates(path, top_n=top_n - len(candidates), progress_callback=progress_callback)
                for i, c in enumerate(smart, start=1):
                    candidates.append(LoopCandidate(c.loop_start, c.loop_end, c.score, c.source, f"内置 Smart Match #{i}"))
                messages.append(f"Internal Smart Match fallback returned {len(smart)} candidate(s).")
            except Exception as exc:
                messages.append(f"Internal Smart Match fallback failed: {exc}")

        if len(candidates) < top_n:
            light = builtin_loop_candidates(path, top_n=top_n - len(candidates), progress_callback=progress_callback)
            if light:
                candidates.extend(light)
            messages.append(f"Lightweight fallback returned {len(light)} candidate(s).")

    info = read_wav_info(path)
    out = _dedupe_and_rank_candidates(candidates, info, top_n)
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
