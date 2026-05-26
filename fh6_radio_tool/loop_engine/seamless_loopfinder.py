from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from ..runtime_tools import bundled_resource_root, candidate_tool_paths, runtime_root


DLL_NAME = "loopfinder.dll" if sys.platform.startswith("win") else "libloopfinder.so"


@dataclass(frozen=True)
class NativeLoopPoint:
    loop_start: int
    loop_end: int
    score: float
    note_diff: float
    loudness_diff: float

    def to_json(self) -> dict:
        return asdict(self)


class _LfLoopPoint(ctypes.Structure):
    _fields_ = [
        ("loopStart", ctypes.c_int64),
        ("loopEnd", ctypes.c_int64),
        ("noteDiff", ctypes.c_float),
        ("loudnessDiff", ctypes.c_float),
        ("score", ctypes.c_float),
    ]


def _unique_paths(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        try:
            key = str(p.resolve()).lower()
        except Exception:
            key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def loopfinder_dll_candidates() -> list[Path]:
    env = os.environ.get("FH6_LOOPFINDER_DLL", "").strip()
    paths: list[Path] = []
    if env:
        paths.append(Path(env))
    paths.extend(candidate_tool_paths(DLL_NAME))

    root = runtime_root()
    resource_root = bundled_resource_root()
    paths.extend([
        root / "third_party" / "loopfinder" / "build" / "Release" / DLL_NAME,
        root / "third_party" / "loopfinder" / "build" / DLL_NAME,
        root / "build_loopfinder" / "Release" / DLL_NAME,
        root / "build_loopfinder" / DLL_NAME,
        resource_root / DLL_NAME,
        resource_root / "tools" / DLL_NAME,
    ])
    return _unique_paths(paths)


def find_loopfinder_dll() -> Path | None:
    for p in loopfinder_dll_candidates():
        if p.exists() and p.is_file():
            return p
    return None


def is_loopfinder_available() -> tuple[bool, str]:
    dll = find_loopfinder_dll()
    if dll is not None:
        return True, str(dll)
    checked = "; ".join(str(p) for p in loopfinder_dll_candidates()[:8])
    return False, f"{DLL_NAME} not found. Checked: {checked}"


def analyze_with_dll(path: Path, dll_path: Path, top_n: int = 8) -> list[NativeLoopPoint]:
    path = Path(path)
    dll_path = Path(dll_path)
    if not path.exists():
        raise FileNotFoundError(path)
    if not dll_path.exists():
        raise FileNotFoundError(dll_path)

    lib = ctypes.CDLL(str(dll_path))
    analyze = lib.lf_analyze_file
    analyze.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(_LfLoopPoint), ctypes.c_int]
    analyze.restype = ctypes.c_int
    get_error = lib.lf_get_last_error
    get_error.argtypes = []
    get_error.restype = ctypes.c_char_p

    capacity = max(1, int(top_n), 10)
    buffer = (_LfLoopPoint * capacity)()
    count = int(analyze(str(path).encode("utf-8"), int(top_n), buffer, capacity))
    if count < 0:
        raw = get_error()
        msg = raw.decode("utf-8", errors="replace") if raw else "unknown loopfinder error"
        raise RuntimeError(msg)

    out: list[NativeLoopPoint] = []
    for i in range(min(count, capacity)):
        item = buffer[i]
        if item.loopEnd <= item.loopStart or item.loopStart < 0:
            continue
        out.append(NativeLoopPoint(
            loop_start=int(item.loopStart),
            loop_end=int(item.loopEnd),
            score=float(item.score),
            note_diff=float(item.noteDiff),
            loudness_diff=float(item.loudnessDiff),
        ))
    return out


def _ascii_safe_input_path(path: Path) -> tuple[Path, Path | None]:
    text = str(path)
    try:
        text.encode("ascii")
        return path, None
    except UnicodeEncodeError:
        pass
    digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:16]
    suffix = path.suffix if path.suffix else ".wav"
    temp_dir = Path(tempfile.gettempdir()) / "fh6_radio_tool_loopfinder"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"loopfinder_input_{digest}{suffix}"
    if not temp_path.exists() or temp_path.stat().st_size != path.stat().st_size:
        shutil.copy2(path, temp_path)
    return temp_path, temp_path


def worker_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FH6 Radio Tool native LoopFinder worker")
    parser.add_argument("--input", required=True)
    parser.add_argument("--top-n", type=int, default=8)
    parser.add_argument("--dll", default="")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    try:
        dll = Path(args.dll) if args.dll else find_loopfinder_dll()
        if dll is None:
            raise FileNotFoundError(f"{DLL_NAME} not found")
        original_input = Path(args.input)
        analysis_input, temp_input = _ascii_safe_input_path(original_input)
        try:
            candidates = analyze_with_dll(analysis_input, dll, top_n=args.top_n)
        finally:
            if temp_input is not None:
                try:
                    temp_input.unlink()
                except Exception:
                    pass
        payload = {
            "ok": True,
            "dll": str(dll),
            "input": str(original_input),
            "analysis_input": str(analysis_input),
            "used_temp_input": temp_input is not None,
            "elapsed_sec": round(time.perf_counter() - started, 3),
            "candidates": [c.to_json() for c in candidates],
        }
        print(json.dumps(payload, ensure_ascii=False), flush=True)
        return 0
    except BaseException as exc:
        payload = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "input": str(Path(args.input)),
            "elapsed_sec": round(time.perf_counter() - started, 3),
        }
        print(json.dumps(payload, ensure_ascii=False), flush=True)
        return 2
