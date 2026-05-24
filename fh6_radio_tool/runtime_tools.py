from __future__ import annotations

import sys
from pathlib import Path


def is_frozen_app() -> bool:
    """Return True when running from PyInstaller/Nuitka-like frozen app."""
    return bool(getattr(sys, "frozen", False))


def runtime_root() -> Path:
    """Writable application root.

    Source mode: project root next to fh6_radio_tool/.
    Frozen one-folder exe mode: folder that contains FH6RadioTool.exe.

    This keeps output/, backup/ and work/ next to the player-facing EXE instead
    of writing into a temporary extraction folder or an arbitrary current folder.
    """
    if is_frozen_app():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def bundled_resource_root() -> Path:
    """Read-only bundled resource root when frozen, otherwise project root."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass).resolve()
    return runtime_root()


def candidate_tool_paths(filename: str) -> list[Path]:
    """Candidate locations for bundled helper executables such as ffmpeg.exe."""
    roots = []
    for r in (runtime_root(), bundled_resource_root(), Path.cwd()):
        try:
            rp = Path(r).resolve()
        except Exception:
            rp = Path(r)
        if rp not in roots:
            roots.append(rp)
    out: list[Path] = []
    for r in roots:
        out.append(r / "tools" / filename)
        out.append(r / filename)
    return out
