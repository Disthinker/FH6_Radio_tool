from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOOPFINDER_SOURCE = PROJECT_ROOT / "third_party" / "loopfinder"
LOOPFINDER_BUILD = PROJECT_ROOT / "build_loopfinder"
TOOLS_DLL = PROJECT_ROOT / "tools" / ("loopfinder.dll" if os.name == "nt" else "libloopfinder.so")
DLL_NAME = TOOLS_DLL.name


def log(message: str) -> None:
    print(message, flush=True)


def run(cmd: list[str], *, cwd: Path | None = None, allow_fail: bool = False) -> int:
    log("  " + " ".join(str(x) for x in cmd))
    proc = subprocess.run(cmd, cwd=str(cwd or PROJECT_ROOT))
    if proc.returncode != 0 and not allow_fail:
        raise RuntimeError(f"Command failed with exit code {proc.returncode}: {' '.join(cmd)}")
    return int(proc.returncode)


def built_dll_candidates() -> list[Path]:
    return [
        TOOLS_DLL,
        LOOPFINDER_BUILD / "Release" / DLL_NAME,
        LOOPFINDER_BUILD / DLL_NAME,
        LOOPFINDER_SOURCE / "build" / "Release" / DLL_NAME,
        LOOPFINDER_SOURCE / "build" / DLL_NAME,
    ]


def find_built_dll() -> Path | None:
    for p in built_dll_candidates():
        if p.exists() and p.is_file():
            return p
    return None


def configure_with_fallback() -> None:
    base_cmd = [
        "cmake",
        "-S",
        str(LOOPFINDER_SOURCE),
        "-B",
        str(LOOPFINDER_BUILD),
        "-DBUILD_TEST=OFF",
        "-DLOOPFINDER_DEBUG_LOG=OFF",
    ]
    if os.name == "nt" and "CMAKE_GENERATOR" not in os.environ:
        first = base_cmd + ["-A", "x64"]
        if run(first, allow_fail=True) == 0:
            return
        shutil.rmtree(LOOPFINDER_BUILD, ignore_errors=True)
    run(base_cmd)


def build_loopfinder(*, clean: bool = False, copy_to_tools: bool = True) -> Path:
    if not LOOPFINDER_SOURCE.exists():
        raise FileNotFoundError(f"LoopFinder source missing: {LOOPFINDER_SOURCE}")
    if clean:
        shutil.rmtree(LOOPFINDER_BUILD, ignore_errors=True)
    LOOPFINDER_BUILD.mkdir(parents=True, exist_ok=True)

    configure_with_fallback()
    cmd = ["cmake", "--build", str(LOOPFINDER_BUILD), "--config", "Release"]
    run(cmd)

    dll = find_built_dll()
    if dll is None:
        raise FileNotFoundError("LoopFinder build completed but DLL was not found.")
    if copy_to_tools:
        TOOLS_DLL.parent.mkdir(parents=True, exist_ok=True)
        if dll.resolve() != TOOLS_DLL.resolve():
            shutil.copy2(dll, TOOLS_DLL)
        dll = TOOLS_DLL
    return dll


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build seamless-loop-music loopfinder native DLL")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--no-copy", action="store_true")
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.check_only:
            dll = find_built_dll()
            if dll is None:
                if args.allow_missing:
                    log("LoopFinder DLL not found.")
                    return 0
                return 1
            log(str(dll))
            return 0
        dll = build_loopfinder(clean=args.clean, copy_to_tools=not args.no_copy)
        log(f"[OK] LoopFinder DLL ready: {dll}")
        return 0
    except Exception as exc:
        if args.allow_missing:
            log(f"[WARN] LoopFinder build unavailable: {exc}")
            return 0
        raise


if __name__ == "__main__":
    raise SystemExit(main())
