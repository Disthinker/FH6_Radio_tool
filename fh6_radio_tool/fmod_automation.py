from __future__ import annotations

"""External Fmod Bank Tools automation helpers.

FH6 Radio Tool does not bundle or link Fmod Bank Tools.  This module only
prepares the external tool's working directories, writes its config.ini, launches
its executable, and tries to trigger GUI actions.  pywinauto is used when it is
healthy; a built-in Win32 fallback is used when pywinauto/comtypes is broken.
"""

import json
import os
import shutil
import struct
import subprocess
import time
import warnings
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .runtime_tools import is_frozen_app


@dataclass(frozen=True)
class FmodToolLayout:
    exe_path: Path
    root_dir: Path
    config_path: Path
    bank_dir: Path
    wav_dir: Path
    rebuild_dir: Path
    cache_dir: Path
    fsb_dir: Path


@dataclass(frozen=True)
class FmodAutomationResult:
    ok: bool
    action: str
    message: str
    layout: FmodToolLayout | None = None
    manifest_path: Path | None = None
    launched_pid: int | None = None
    output_files: list[Path] | None = None

    def to_json(self) -> dict:
        data = asdict(self)
        # dataclasses.asdict keeps nested Path values as Path objects; normalize.
        def norm(v):
            if isinstance(v, Path):
                return str(v)
            if isinstance(v, dict):
                return {k: norm(x) for k, x in v.items()}
            if isinstance(v, list):
                return [norm(x) for x in v]
            return v
        return norm(data)



_COMTYPES_REPAIR_ATTEMPTED = False


def _automation_win32_fallback_available() -> bool:
    """Return whether the built-in ctypes/Win32 GUI trigger can be used.

    v3.0.38: pywinauto can fail in otherwise valid Windows environments with
    errors such as "Typelib different than module" from a stale comtypes cache.
    The one-click workflow should not be blocked by that import error, because
    Fmod Bank Tools can also be triggered with normal Win32 window messages and
    its native Ctrl+E / Ctrl+B shortcuts.
    """
    return os.name == "nt"


def _purge_partial_automation_modules() -> None:
    """Drop partially imported pywinauto/comtypes generated modules before retry."""
    import sys

    for name in list(sys.modules):
        if name == "pywinauto" or name.startswith("pywinauto.") or name.startswith("comtypes.gen."):
            sys.modules.pop(name, None)


def _clear_comtypes_generated_cache() -> list[str]:
    """Best-effort cleanup for stale comtypes generated typelib modules.

    The common Windows failure is:
        ImportError: Typelib different than module
    It is usually caused by old files in comtypes.gen / comtypes_cache.  Clearing
    generated files is safe: comtypes regenerates them when needed.  This helper
    never raises; it returns a short list of touched paths for diagnostics.
    """
    global _COMTYPES_REPAIR_ATTEMPTED
    if _COMTYPES_REPAIR_ATTEMPTED:
        return []
    _COMTYPES_REPAIR_ATTEMPTED = True

    touched: list[str] = []
    candidates: list[Path] = []
    try:
        import tempfile
        candidates.append(Path(tempfile.gettempdir()) / "comtypes_cache")
    except Exception:
        pass
    try:
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "Temp" / "comtypes_cache")
    except Exception:
        pass
    try:
        import comtypes.gen  # type: ignore
        for item in getattr(comtypes.gen, "__path__", []) or []:
            candidates.append(Path(item))
    except Exception:
        pass

    seen: set[Path] = set()
    for cand in candidates:
        try:
            cand = cand.resolve()
        except Exception:
            continue
        if cand in seen or not cand.exists() or not cand.is_dir():
            continue
        seen.add(cand)
        name = cand.name.lower()
        if name not in {"gen", "comtypes_cache"}:
            continue
        try:
            for child in cand.iterdir():
                if child.name == "__init__.py":
                    continue
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                    touched.append(str(child))
                elif child.suffix.lower() in {".py", ".pyc", ".pyo"}:
                    child.unlink(missing_ok=True)
                    touched.append(str(child))
        except Exception:
            continue
    return touched


def _import_pywinauto_for_trigger():
    """Import pywinauto trigger pieces, repairing stale comtypes cache once."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from pywinauto import Application  # type: ignore
            from pywinauto.keyboard import send_keys  # type: ignore
        return Application, send_keys, ""
    except Exception as first_exc:
        msg = f"{type(first_exc).__name__}: {first_exc}"
        if "typelib" in msg.lower() or "comtypes" in msg.lower():
            cleared = _clear_comtypes_generated_cache()
            _purge_partial_automation_modules()
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    from pywinauto import Application  # type: ignore
                    from pywinauto.keyboard import send_keys  # type: ignore
                suffix = f"; repaired comtypes cache entries={len(cleared)}" if cleared else "; retried after comtypes cache check"
                return Application, send_keys, suffix
            except Exception as second_exc:
                return None, None, msg + f"; retry failed: {type(second_exc).__name__}: {second_exc}"
        return None, None, msg


def pywinauto_status() -> tuple[bool, str]:
    """Return whether automatic GUI control is available, plus diagnostics.

    Kept under the old function name for compatibility with the UI code.  Since
    v3.0.37, a broken pywinauto/comtypes import no longer means the whole one-
    click workflow is unavailable on Windows: the tool can fall back to built-in
    ctypes/Win32 button/shortcut triggering.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import pywinauto  # type: ignore
        Application, send_keys, repair_msg = _import_pywinauto_for_trigger()
        if Application and send_keys:
            version = getattr(pywinauto, "__version__", "unknown")
            return True, f"pywinauto OK, version={version}{repair_msg}"
    except Exception:
        pass

    Application, send_keys, detail = _import_pywinauto_for_trigger()
    if Application and send_keys:
        return True, f"pywinauto OK{detail}"
    if _automation_win32_fallback_available():
        return True, f"pywinauto unavailable ({detail}); using built-in Win32 fallback"
    return False, detail or "pywinauto unavailable and Win32 fallback is not available on this platform"


def is_pywinauto_available() -> bool:
    """Return whether automatic Fmod GUI triggering is available."""
    return pywinauto_status()[0]


def pywinauto_user_hint() -> str:
    ok, detail = pywinauto_status()
    if ok:
        return detail
    if is_frozen_app():
        return (
            f"自动控制组件不可用：{detail}。"
            "请使用 v3.0.37 或更新版本重新下载/重新打包；不要运行旧版 v2 的 setup_env.bat 修复 EXE。"
        )
    return (
        f"自动控制组件不可用：{detail}。"
        "如果是在开发者源码环境中运行，请先执行 setup_env.bat 或 pip install -r requirements.txt；"
        "如果出现 Typelib different than module，请删除旧虚拟环境后用 v3.0.37 重新安装，或直接使用 PyInstaller 打包版。"
    )

def layout_from_exe(exe_path: Path) -> FmodToolLayout:
    exe_path = Path(exe_path)
    if not exe_path.exists():
        raise FileNotFoundError(f"Fmod Bank Tools exe 不存在: {exe_path}")
    root = exe_path.parent
    return FmodToolLayout(
        exe_path=exe_path,
        root_dir=root,
        config_path=root / "config.ini",
        bank_dir=root / "bank",
        wav_dir=root / "wav",
        rebuild_dir=root / "build",
        cache_dir=root / "fsbcache",
        fsb_dir=root / "fsb",
    )


def ensure_layout_dirs(layout: FmodToolLayout) -> None:
    for d in (layout.bank_dir, layout.wav_dir, layout.rebuild_dir, layout.cache_dir, layout.fsb_dir):
        d.mkdir(parents=True, exist_ok=True)


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _qsettings_ini_path(path: Path) -> str:
    """Return a path string safe for Qt QSettings INI values.

    Fmod Bank Tools reads config.ini through Qt QSettings.  Raw Windows
    backslashes can be interpreted as INI escape sequences by QSettings.
    Therefore we write forward-slash paths such as `E:/Temp/modBankTool/bank`.
    If raw backslashes are written, Fmod Bank Tools may display a malformed
    path such as `E:empmodBankToolank` and then report `No bank files found`.
    Forward slashes are valid Windows paths and are preserved by QSettings.
    """
    return Path(path).as_posix()


def write_config_ini(
    layout: FmodToolLayout,
    *,
    format_name: str = "vorbis",
    quality: int = 75,
    cpu_threads: int = 2,
    default_settings: bool = True,
    encode_sync_point: bool = True,
    looping: bool = True,
    embeded_file_names: bool = True,
    write_peak_volume: bool = True,
) -> Path:
    ensure_layout_dirs(layout)
    text = "\n".join([
        "[Directorys]",
        f"BankDir={_qsettings_ini_path(layout.bank_dir)}",
        f"WavDir={_qsettings_ini_path(layout.wav_dir)}",
        f"RebuildDir={_qsettings_ini_path(layout.rebuild_dir)}",
        f"CacheDir={_qsettings_ini_path(layout.cache_dir)}",
        "",
        "[Options]",
        f"Format={format_name}",
        f"Quality={int(quality)}",
        f"CPUThreads={int(cpu_threads)}",
        f"DefaultSettings={_bool_text(default_settings)}",
        f"EncodeSyncPoint={_bool_text(encode_sync_point)}",
        f"Looping={_bool_text(looping)}",
        f"EmbededFileNames={_bool_text(embeded_file_names)}",
        f"WritePeakVolume={_bool_text(write_peak_volume)}",
        "",
    ])
    layout.config_path.write_text(text, encoding="utf-8")
    return layout.config_path


def _clean_directory(path: Path, keep_names: set[str] | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    keep_names = keep_names or set()
    for child in path.iterdir():
        if child.name in keep_names:
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except FileNotFoundError:
                pass


def _scan_for_fsb_magic(path: Path, *, scan_limit_mb: int | None = None) -> bool:
    """Cheap fallback scan for FSB magic bytes."""
    path = Path(path)
    if not path.exists() or not path.is_file():
        return False
    magic = (b"FSB5", b"FSB4", b"FSB3")
    chunk_size = 1024 * 1024
    remaining = None if scan_limit_mb is None else max(1, int(scan_limit_mb)) * chunk_size
    tail = b""
    try:
        with path.open("rb") as fh:
            while True:
                if remaining is not None and remaining <= 0:
                    break
                read_size = chunk_size if remaining is None else min(chunk_size, remaining)
                data = fh.read(read_size)
                if not data:
                    break
                hay = tail + data
                if any(m in hay for m in magic):
                    return True
                tail = hay[-8:]
                if remaining is not None:
                    remaining -= len(data)
    except OSError:
        return False
    return False


def fmod_bank_extractable_fsb_info(path: Path) -> dict:
    """Inspect a .bank with rules close to Fmod Bank Tools' extractor.

    Fmod Bank Tools does not merely search for the string ``FSB5``.  Its
    ``bank_extract`` routine expects a RIFF/FEV bank and then reads the SNDH
    chunk to get embedded FSB offsets and sizes.  Banks can therefore look like
    a valid FMOD bank but still fail with ``can't find any fsb audio`` when the
    SNDH chunk is empty or points to zero-sized audio.

    This preflight mirrors that logic so FH6 Radio Tool can skip metadata-only
    banks before launching the external GUI.
    """
    path = Path(path)
    info = {
        "path": str(path),
        "exists": path.exists(),
        "valid_fmod_bank": False,
        "extractable": False,
        "encrypted_or_non_fsb5": False,
        "fsb_count": 0,
        "reason": "not_checked",
    }
    if not path.exists() or not path.is_file():
        info["reason"] = "missing_file"
        return info
    try:
        data = path.read_bytes()
    except OSError as exc:
        info["reason"] = f"read_error:{exc}"
        return info
    size = len(data)
    if size < 0x30:
        info["reason"] = "too_small"
        return info
    if data[0:4] != b"RIFF" or data[8:12] != b"FEV ":
        info["reason"] = "not_riff_fev_bank"
        return info
    info["valid_fmod_bank"] = True
    if data[0x1c:0x20] != b"LIST":
        info["reason"] = "missing_list_chunk"
        return info

    pos = 0x24
    if data[pos:pos + 4] == b"PROJ" and data[pos + 4:pos + 8] == b"BNKI":
        pos += 8
        if pos + 4 <= size:
            chunk_size = struct.unpack_from("<I", data, pos)[0]
            pos += 4 + chunk_size
    else:
        # Keep scanning from the conventional area.  Some variants may have
        # extra chunks, and a direct SNDH search below is more forgiving.
        pos = 0x20

    sndh_positions: list[int] = []
    scan_pos = max(0, min(pos, size - 8))
    while True:
        idx = data.find(b"SNDH", scan_pos)
        if idx < 0:
            break
        sndh_positions.append(idx)
        scan_pos = idx + 4

    if not sndh_positions:
        info["reason"] = "missing_sndh_chunk"
        return info

    for idx in sndh_positions:
        if idx + 12 > size:
            continue
        chunk_size = struct.unpack_from("<I", data, idx + 4)[0]
        if chunk_size == 0 or chunk_size == 0xFFFFFFFF:
            continue
        if chunk_size < 12:
            continue
        # Fmod Bank Tools treats (chunk_size - 4) / 8 as the fsb count after an
        # unknown 4-byte value.  Be defensive for malformed files.
        fsb_count = max(0, (chunk_size - 4) // 8)
        pairs_start = idx + 12
        for i in range(fsb_count):
            off_pos = pairs_start + i * 8
            if off_pos + 8 > size:
                break
            fsb_off, fsb_size = struct.unpack_from("<II", data, off_pos)
            if fsb_off == 0 or fsb_size == 0:
                continue
            if fsb_off >= size:
                continue
            info["fsb_count"] = fsb_count
            fsb_magic = data[fsb_off:fsb_off + 4]
            info["encrypted_or_non_fsb5"] = fsb_magic != b"FSB5"
            info["extractable"] = True
            info["reason"] = "sndh_offsets_present" if fsb_magic == b"FSB5" else "sndh_offsets_present_but_fsb_magic_not_fsb5_or_encrypted"
            return info

    info["reason"] = "sndh_has_no_nonzero_fsb_offsets"
    return info


def bank_contains_fsb_audio(path: Path, *, scan_limit_mb: int | None = None) -> bool:
    """Return True only when Fmod Bank Tools is likely able to extract FSB.

    Earlier builds trusted the ``R*_Tracks_CU1.assets.bank`` name too much or
    used a raw FSB magic scan.  The external tool itself fails with
    ``can't find any fsb audio`` when SNDH offsets are missing, so the main
    test now follows that rule.  A magic scan is kept only for non-standard
    banks that are not RIFF/FEV but visibly contain FSB payloads.
    """
    info = fmod_bank_extractable_fsb_info(Path(path))
    if info.get("extractable"):
        return True
    # Fallback only for non-standard containers.  If it is a valid RIFF/FEV FMOD
    # bank with empty SNDH, feeding it to Fmod Bank Tools will reproduce the
    # user's error, so keep it as False.
    if not info.get("valid_fmod_bank"):
        return _scan_for_fsb_magic(Path(path), scan_limit_mb=scan_limit_mb)
    return False


def bank_preflight_message(path: Path) -> str:
    info = fmod_bank_extractable_fsb_info(Path(path))
    return f"{Path(path).name}: extractable={info.get('extractable')} reason={info.get('reason')} fsb_count={info.get('fsb_count')}"


def is_preferred_radio_cu1_bank(path: Path) -> bool:
    """Return True for the normal FH radio audio bank name pattern.

    In current FH radio replacements the bank users usually need is named like
    R5_Tracks_CU1.assets.bank.  Static FSB magic scans can be unreliable for
    some banks, so this name pattern is trusted and should not be replaced by
    a fallback Disk bank merely because a quick scan failed.
    """
    name = Path(path).name.lower().replace('-', '_')
    return name.endswith('.bank') and '_tracks_cu1.assets.bank' in name and name.startswith('r')

def filter_audio_banks(bank_paths: Iterable[Path]) -> tuple[list[Path], list[Path]]:
    """Split bank paths into (banks_with_fsb_audio, skipped_metadata_banks)."""
    audio: list[Path] = []
    skipped: list[Path] = []
    for src in bank_paths:
        p = Path(src)
        if bank_contains_fsb_audio(p):
            audio.append(p)
        else:
            skipped.append(p)
    return audio, skipped


def discover_related_audio_banks(
    bank_paths: Iterable[Path],
    *,
    search_root: Path | None = None,
    max_results: int = 1,
    preferred_tokens: Iterable[str] | None = None,
) -> list[Path]:
    """Try to find nearby audio-carrying banks when XML-declared banks are metadata-only.

    The FH bank layout is not guaranteed to make every XML-declared bank an
    audio container.  When selected banks contain no FSB chunks, this helper
    scans the same FMODBanks tree and prefers files that share obvious station
    prefixes such as R1/R2/... or contain "track".  It is a fallback only;
    exact XML-declared audio banks always win.
    """
    original = [Path(p) for p in bank_paths]
    roots: list[Path] = []
    if search_root:
        roots.append(Path(search_root))
    roots.extend(p.parent for p in original if p.parent.exists())
    # Deduplicate while preserving order.
    seen_roots: set[Path] = set()
    roots = [r for r in roots if not (r in seen_roots or seen_roots.add(r))]

    station_tokens: set[str] = set()
    for p in original:
        stem = p.stem.lower()
        for part in stem.replace('-', '_').split('_'):
            if part and (part.startswith('r') and any(ch.isdigit() for ch in part)):
                station_tokens.add(part)
        if 'tracks' in stem:
            station_tokens.add('tracks')

    preferred = {str(t).strip().lower() for t in (preferred_tokens or []) if str(t).strip()}

    def name_matches_preferred(path: Path) -> bool:
        if not preferred:
            return True
        stem = path.stem.lower().replace('-', '_')
        parts = [p for p in stem.split('_') if p]
        return any(t in parts or stem.startswith(t + '_') or ('_' + t + '_') in ('_' + stem + '_') for t in preferred)

    candidates: list[tuple[int, Path]] = []
    seen_files: set[Path] = set()
    for root in roots:
        try:
            files = list(root.rglob('*.bank'))
        except OSError:
            files = []
        for f in files:
            if f in seen_files:
                continue
            seen_files.add(f)
            name = f.stem.lower()
            if 'master' in name or 'strings' in name:
                continue
            if not bank_contains_fsb_audio(f):
                continue
            if preferred and not name_matches_preferred(f):
                continue
            score = 0
            if 'track' in name:
                score -= 50
            norm_name = f.name.lower().replace('-', '_').replace('.', '_')
            if 'tracks' in norm_name and 'cu1' in norm_name and 'assets' in norm_name:
                score -= 250
            for t in station_tokens:
                if t and t in name:
                    score -= 25
            # Prefer shallower/direct files and smaller candidate set.
            score += len(str(f)) // 20
            candidates.append((score, f))
    return [p for _, p in sorted(candidates, key=lambda x: (x[0], str(x[1]).lower()))[:max_results]]


def copy_banks_to_tool_bank_dir(bank_paths: Iterable[Path], layout: FmodToolLayout, *, clean_bank_dir: bool = True) -> list[Path]:
    ensure_layout_dirs(layout)
    if clean_bank_dir:
        # Keep password files because encrypted banks may need them.
        keep = {p.name for p in layout.bank_dir.glob("*.txt")}
        _clean_directory(layout.bank_dir, keep_names=keep)
    copied: list[Path] = []
    for src in bank_paths:
        src = Path(src)
        if not src.exists():
            raise FileNotFoundError(f"bank 文件不存在: {src}")
        dst = layout.bank_dir / src.name
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        copied.append(dst)
    return copied


def import_wav_workspace_to_tool(src_wav_dir: Path, layout: FmodToolLayout, *, clean_wav_dir: bool = True) -> Path:
    src_wav_dir = Path(src_wav_dir)
    if not src_wav_dir.exists():
        raise FileNotFoundError(f"wav 工作区不存在: {src_wav_dir}")
    ensure_layout_dirs(layout)
    if clean_wav_dir:
        _clean_directory(layout.wav_dir)
    for child in src_wav_dir.iterdir():
        dst = layout.wav_dir / child.name
        if child.is_dir():
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(child, dst)
        else:
            shutil.copy2(child, dst)
    return layout.wav_dir


def collect_rebuilt_banks(layout: FmodToolLayout, output_dir: Path, *, clean_output_dir: bool = False) -> list[Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if clean_output_dir:
        _clean_directory(output_dir)
    copied: list[Path] = []
    for src in sorted(layout.rebuild_dir.glob("*.bank")):
        dst = output_dir / src.name
        shutil.copy2(src, dst)
        copied.append(dst)
    return copied



def _terminate_process(proc: subprocess.Popen | None, *, timeout_sec: float = 3.0) -> None:
    """Best-effort cleanup for the external Fmod Bank Tools GUI process.

    Developer scans may launch the tool many times.  Always close the process
    after Extract/Rebuild finishes or fails so stale windows do not accumulate
    and confuse the next automation action.
    """
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
    except Exception:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=timeout_sec)
            return
        except subprocess.TimeoutExpired:
            pass
    except Exception:
        pass
    try:
        if proc.poll() is None:
            proc.kill()
    except Exception:
        pass

def launch_tool(layout: FmodToolLayout) -> subprocess.Popen:
    ensure_layout_dirs(layout)
    # Important: Fmod Bank Tools relies on files next to its exe (FMOD/FSBank
    # DLLs, fsb folder and config.ini).  Always launch with cwd set to the exe
    # root instead of FH6 Radio Tool's process directory.
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        [str(layout.exe_path)],
        cwd=str(layout.root_dir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )


def _find_main_window_by_pid(pid: int) -> int | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None

    user32 = ctypes.windll.user32
    hwnds: list[int] = []

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            proc_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
            if proc_id.value == pid:
                hwnds.append(int(hwnd))
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(EnumWindowsProc(callback), 0)
    except Exception:
        return None
    return hwnds[0] if hwnds else None


def _window_text(hwnd: int) -> str:
    try:
        import ctypes
        user32 = ctypes.windll.user32
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception:
        return ""


def _class_name(hwnd: int) -> str:
    try:
        import ctypes
        user32 = ctypes.windll.user32
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        return buf.value
    except Exception:
        return ""



def _click_client_point(hwnd: int, x: int, y: int) -> tuple[bool, str]:
    """Physically click a client-area point in a top-level window.

    Fmod Bank Tools exposes Extract/Rebuild as Qt toolbar actions.  Some Windows
    builds do not expose those actions as normal child Button controls, so a
    final robust option is to click the toolbar slot directly.  The caller only
    uses this after restoring/focusing the window, and real Extract/Rebuild
    completion is still verified by output files.
    """
    if os.name != "nt":
        return False, "not Windows"
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        pt = wintypes.POINT(int(x), int(y))
        if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
            return False, "ClientToScreen failed"
        user32.SetCursorPos(pt.x, pt.y)
        # mouse_event is deprecated but universally available and enough here.
        MOUSEEVENTF_LEFTDOWN = 0x0002
        MOUSEEVENTF_LEFTUP = 0x0004
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.06)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        return True, f"clicked client=({x},{y}) screen=({pt.x},{pt.y})"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

def _trigger_gui_action_win32(pid: int, action: str, timeout_sec: int = 12) -> tuple[bool, str]:
    """Trigger Extract/Rebuild using only built-in ctypes Win32 APIs.

    This path avoids pywinauto and comtypes entirely.  It first tries to find a
    visible child button/menu item containing the action text and sends BM_CLICK.
    If that is not possible, it focuses the main window and sends the tool's native Ctrl+E/Ctrl+B shortcuts.
    The caller still waits for real output files, so a false positive here will
    be caught by the normal timeout/output verification logic.
    """
    if os.name != "nt":
        return False, "Win32 fallback is only available on Windows."
    action_lower = action.lower().strip()
    if action_lower not in {"extract", "rebuild"}:
        return False, f"未知 action: {action}"
    try:
        import ctypes
        from ctypes import wintypes
    except Exception as exc:
        return False, f"Win32 fallback unavailable: {type(exc).__name__}: {exc}"

    user32 = ctypes.windll.user32
    BM_CLICK = 0x00F5
    SW_RESTORE = 9
    VK_CONTROL = 0x11
    VK_E = 0x45
    VK_B = 0x42
    KEYEVENTF_KEYUP = 0x0002

    deadline = time.time() + timeout_sec
    last_error = ""
    while time.time() < deadline:
        hwnd = _find_main_window_by_pid(pid)
        if not hwnd:
            last_error = "main window not found"
            time.sleep(0.25)
            continue
        try:
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

        clicked: list[str] = []
        EnumChildProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def child_callback(child_hwnd, lparam):
            if clicked:
                return False
            try:
                text = _window_text(int(child_hwnd)).strip().lower()
                cls = _class_name(int(child_hwnd)).strip().lower()
                if action_lower in text and ("button" in cls or "menu" in cls or text == action_lower):
                    user32.SendMessageW(child_hwnd, BM_CLICK, 0, 0)
                    clicked.append(text or cls or str(int(child_hwnd)))
                    return False
            except Exception:
                pass
            return True

        try:
            user32.EnumChildWindows(hwnd, EnumChildProc(child_callback), 0)
        except Exception as exc:
            last_error = f"EnumChildWindows failed: {exc}"
        if clicked:
            return True, f"已通过内置 Win32 fallback 点击 {action} 控件：{clicked[0]}。"

        # Fmod Bank Tools v2.x keeps Extract/Rebuild in a top toolbar.
        # Qt toolbar actions are not always exposed as child Button controls,
        # so click the native toolbar slot directly before falling back to the
        # keyboard accelerator.  Typical client coordinates: Extract around
        # x=16..28, Rebuild around x=50..70, y just below the menu bar.
        toolbar_x = 22 if action_lower == "extract" else 58
        toolbar_ok, toolbar_msg = _click_client_point(hwnd, toolbar_x, 40)
        if toolbar_ok:
            return True, f"已通过内置 Win32 fallback 点击 Fmod Bank Tools 工具栏 {action}：{toolbar_msg}。"
        last_error = f"toolbar click failed: {toolbar_msg}"

        try:
            key = VK_E if action_lower == "extract" else VK_B
            shortcut = "Ctrl+E" if action_lower == "extract" else "Ctrl+B"
            user32.keybd_event(VK_CONTROL, 0, 0, 0)
            user32.keybd_event(key, 0, 0, 0)
            user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
            return True, f"已通过内置 Win32 fallback 发送 {shortcut} 触发 {action}。"
        except Exception as exc:
            last_error = str(exc)
            time.sleep(0.25)
    return False, f"内置 Win32 fallback 未能触发 {action}：{last_error or '窗口未就绪'}。"


def try_trigger_gui_action(pid: int, action: str, timeout_sec: int = 12) -> tuple[bool, str]:
    """Best-effort GUI automation with pywinauto plus a Win32 fallback."""
    action_lower = action.lower().strip()
    if action_lower not in {"extract", "rebuild"}:
        return False, f"未知 action: {action}"

    Application, send_keys, import_detail = _import_pywinauto_for_trigger()
    if Application and send_keys:
        deadline = time.time() + timeout_sec
        last_error = ""
        while time.time() < deadline:
            try:
                app = Application(backend="win32").connect(process=pid)
                win = app.top_window()
                win.set_focus()
                # Try menu/button text first; UI labels differ by release, so keep this fuzzy.
                for ctrl in win.descendants():
                    try:
                        text = (ctrl.window_text() or "").strip().lower()
                        cls = (ctrl.friendly_class_name() or "").lower()
                        if action_lower in text and any(k in cls for k in ("button", "menu", "tool")):
                            ctrl.click_input()
                            return True, f"已通过 pywinauto(win32) 点击 {action} 控件：{text or cls}。"
                    except Exception:
                        continue
                if action_lower == "extract":
                    send_keys("^e")
                    shortcut = "Ctrl+E"
                else:
                    send_keys("^b")
                    shortcut = "Ctrl+B"
                return True, f"已通过 pywinauto(win32) 发送 {shortcut} 尝试触发 {action}。"
            except Exception as exc:
                last_error = str(exc)
                time.sleep(0.5)
        win32_ok, win32_msg = _trigger_gui_action_win32(pid, action, timeout_sec=max(3, min(timeout_sec, 12)))
        if win32_ok:
            return True, win32_msg + f" pywinauto 未成功：{last_error or import_detail}"
        return False, f"未能自动触发 {action}：pywinauto={last_error or import_detail}; win32={win32_msg}；请手动点击。"

    win32_ok, win32_msg = _trigger_gui_action_win32(pid, action, timeout_sec=timeout_sec)
    if win32_ok:
        return True, win32_msg + f" pywinauto 不可用：{import_detail}"
    return False, f"未能自动触发 {action}：pywinauto={import_detail}; win32={win32_msg}；请手动点击。"


def write_manifest(path: Path, action: str, layout: FmodToolLayout, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": "2.7.10",
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "action": action,
        "layout": {
            "exe_path": str(layout.exe_path),
            "root_dir": str(layout.root_dir),
            "config_path": str(layout.config_path),
            "bank_dir": str(layout.bank_dir),
            "wav_dir": str(layout.wav_dir),
            "rebuild_dir": str(layout.rebuild_dir),
            "cache_dir": str(layout.cache_dir),
            "fsb_dir": str(layout.fsb_dir),
        },
        "payload": payload,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def prepare_extract_job(
    exe_path: Path,
    bank_paths: Iterable[Path],
    manifest_path: Path,
    *,
    clean_bank_dir: bool = True,
    search_root: Path | None = None,
    preferred_tokens: Iterable[str] | None = None,
) -> FmodAutomationResult:
    layout = layout_from_exe(exe_path)
    write_config_ini(layout)
    # Clean previous outputs so one-click waiting cannot falsely succeed on stale files.
    _clean_directory(layout.wav_dir)
    _clean_directory(layout.fsb_dir)

    requested = [Path(p) for p in bank_paths]
    missing = [p for p in requested if not p.exists()]
    if missing:
        raise FileNotFoundError("bank 文件不存在: " + ", ".join(str(p) for p in missing))

    # Strongly prefer explicit R*_Tracks_CU1.assets.bank requests.  These are
    # the normal FH radio audio banks for replacement.  Do not let a static FSB
    # preflight failure redirect the workflow to R*_Tracks_Disk.assets.bank.
    preferred_requested = [p for p in requested if is_preferred_radio_cu1_bank(p)]
    discovered_banks: list[Path] = []
    cu1_preflight_warnings: list[str] = []
    if preferred_requested:
        # Prefer CU1 only when Fmod Bank Tools can actually extract FSB offsets
        # from it.  If CU1 is metadata-only, launching the external GUI produces
        # exactly the confusing "can't find any fsb audio" error seen by users.
        valid_preferred, invalid_preferred = filter_audio_banks(preferred_requested)
        if valid_preferred:
            # v3.0.23: keep every explicitly requested same-station Track bank.
            # Static preflight scans can fail on small/supplemental Track banks
            # such as R1_Tracks_Disk even though Fmod Bank Tools can extract
            # them.  Filtering those banks out here made the UI show the Disk
            # slots but the Extract template still contained only CU1, so the
            # Disk songs could never be replaced.
            #
            # Only non-Track banks still require a positive static FSB preflight.
            audio_banks = []
            skipped_banks = []

            def _explicit_station_track_bank(path: Path) -> bool:
                name = path.name.lower().replace('-', '_')
                return (
                    'tracks' in name
                    and 'stinger' not in name
                    and '_dj' not in name
                    and not name.startswith('vo_')
                )

            for p in requested:
                if _explicit_station_track_bank(p):
                    if p not in audio_banks:
                        audio_banks.append(p)
                elif bank_contains_fsb_audio(p):
                    if p not in audio_banks:
                        audio_banks.append(p)
                else:
                    skipped_banks.append(p)
        else:
            audio_banks = []
            skipped_banks = invalid_preferred + [p for p in requested if p not in invalid_preferred and not bank_contains_fsb_audio(p)]
            cu1_preflight_warnings = [bank_preflight_message(p) for p in invalid_preferred]
            discovered_banks = discover_related_audio_banks(requested, search_root=search_root, preferred_tokens=preferred_tokens)
            audio_banks = discovered_banks
    else:
        audio_banks, skipped_banks = filter_audio_banks(requested)
        if not audio_banks:
            discovered_banks = discover_related_audio_banks(requested, search_root=search_root, preferred_tokens=preferred_tokens)
            audio_banks = discovered_banks

    if not audio_banks:
        manifest = write_manifest(manifest_path, "extract", layout, {
            "requested_banks": [str(p) for p in requested],
            "skipped_no_fsb_audio": [str(p) for p in skipped_banks],
            "discovered_audio_banks": [],
            "cu1_preflight_warnings": cu1_preflight_warnings,
            "bank_preflight": [bank_preflight_message(p) for p in requested],
            "error": "No selected or related banks contain extractable FSB offsets.",
        })
        msg = (
            "未找到 Fmod Bank Tools 可提取的 FSB 音频 bank。已跳过："
            + ", ".join(p.name for p in skipped_banks)
            + "。原因通常不是 fsb 文件夹缺失，而是该 bank 的 SNDH 里没有有效 FSB 偏移。"
            + (" 预检查：" + "; ".join(cu1_preflight_warnings) if cu1_preflight_warnings else "")
            + " 请确认游戏根目录和 FMODBanks 目录是否正确。"
        )
        return FmodAutomationResult(False, "extract_prepare", msg, layout, manifest, None, [])

    copied = copy_banks_to_tool_bank_dir(audio_banks, layout, clean_bank_dir=clean_bank_dir)
    payload = {
        "requested_banks": [str(p) for p in requested],
        "copied_audio_banks": [str(p) for p in copied],
        "skipped_no_fsb_audio": [str(p) for p in skipped_banks],
        "discovered_audio_banks": [str(p) for p in discovered_banks],
        "cu1_preflight_warnings": cu1_preflight_warnings,
        "bank_preflight": [bank_preflight_message(p) for p in requested],
        "preferred_station_tokens": [str(t) for t in (preferred_tokens or [])],
        "tool_root_dir": str(layout.root_dir),
        "launch_cwd": str(layout.root_dir),
        "config_path": str(layout.config_path),
    }
    manifest = write_manifest(manifest_path, "extract", layout, payload)
    msg = f"已准备 Extract：复制 {len(copied)} 个 Fmod Bank Tools 可提取的音频 bank。"
    if skipped_banks:
        msg += " 已跳过不含 FSB 音频的 bank：" + ", ".join(p.name for p in skipped_banks) + "。"
    if cu1_preflight_warnings:
        msg += " CU1 bank 预检查未发现可提取 FSB：" + "; ".join(cu1_preflight_warnings) + "。"
    if discovered_banks:
        msg += " 已自动改用同电台可提取音频 bank：" + ", ".join(p.name for p in discovered_banks) + "。"
    msg += f" 工作目录={layout.root_dir}。"
    return FmodAutomationResult(True, "extract_prepare", msg, layout, manifest, None, copied)


def prepare_rebuild_job(exe_path: Path, wav_workspace: Path, manifest_path: Path, *, clean_wav_dir: bool = True) -> FmodAutomationResult:
    layout = layout_from_exe(exe_path)
    write_config_ini(layout)
    # Clean previous build output so deploy never uses stale .bank files.  Use
    # retry + verification because Windows may keep files locked when the user
    # left Fmod Bank Tools open from a previous run.
    leftovers = _clean_directory_with_retry(layout.rebuild_dir)
    if leftovers:
        names = ", ".join(p.name for p in leftovers)
        raise RuntimeError(
            "无法清理 Fmod Bank Tools build 目录中的旧输出文件：" + names +
            "。请关闭所有 Fmod Bank Tools 窗口后重试，避免一键替换误判旧 bank。"
        )
    wav_dir = import_wav_workspace_to_tool(wav_workspace, layout, clean_wav_dir=clean_wav_dir)
    manifest = write_manifest(manifest_path, "rebuild", layout, {
        "wav_workspace": str(wav_workspace),
        "tool_wav_dir": str(wav_dir),
        "rebuild_dir_cleaned": True,
        "stale_build_files_blocked": [],
    })
    return FmodAutomationResult(True, "rebuild_prepare", f"已准备 Rebuild wav 工作区：{wav_dir}；旧 build 输出已清理。", layout, manifest, None, [wav_dir])


def launch_and_optionally_trigger(exe_path: Path, action: str, *, auto_trigger: bool = True) -> FmodAutomationResult:
    layout = layout_from_exe(exe_path)
    proc = launch_tool(layout)
    message = f"已启动 Fmod Bank Tools，PID={proc.pid}。"
    ok = True
    if auto_trigger:
        trig_ok, trig_msg = try_trigger_gui_action(proc.pid, action)
        ok = trig_ok
        message += " " + trig_msg
    return FmodAutomationResult(ok, action, message, layout, None, proc.pid, None)



def _matching_files(path: Path, patterns: tuple[str, ...], *, recursive: bool = True) -> list[Path]:
    """Return unique matching files in deterministic order."""
    path = Path(path)
    files: list[Path] = []
    if path.exists():
        for pat in patterns:
            iterator = path.rglob(pat) if recursive else path.glob(pat)
            files.extend(x for x in iterator if x.is_file())
    return sorted(set(files), key=lambda x: x.as_posix().lower())


def _file_snapshot(path: Path, patterns: tuple[str, ...], *, recursive: bool = True) -> dict[str, tuple[int, int]]:
    """Return a lightweight name -> (size, mtime_ns) snapshot.

    Rebuild detection must not be fooled by old bank files left from a previous
    run.  Tracking mtime/size lets us tell whether the current Rebuild actually
    created or updated a bank after we clicked the button.
    """
    snap: dict[str, tuple[int, int]] = {}
    for f in _matching_files(path, patterns, recursive=recursive):
        try:
            st = f.stat()
            snap[f.name.lower()] = (int(st.st_size), int(st.st_mtime_ns))
        except OSError:
            continue
    return snap


def _dir_signature(path: Path, patterns: tuple[str, ...]) -> tuple[int, int]:
    """Return (file_count, total_size) for files matching patterns."""
    unique = _matching_files(path, patterns)
    total = 0
    for f in unique:
        try:
            total += int(f.stat().st_size)
        except OSError:
            pass
    return len(unique), total


def _files_changed_since_baseline(path: Path, patterns: tuple[str, ...], baseline: dict[str, tuple[int, int]] | None) -> set[str]:
    if baseline is None:
        return {p.name.lower() for p in _matching_files(path, patterns, recursive=False)}
    changed: set[str] = set()
    current = _file_snapshot(path, patterns, recursive=False)
    for name, sig in current.items():
        if baseline.get(name) != sig:
            changed.add(name)
    return changed


def _clean_directory_with_retry(path: Path, *, keep_names: set[str] | None = None, retries: int = 5, delay_sec: float = 0.35) -> list[Path]:
    """Clean a directory and return entries that could not be removed.

    External Fmod Bank Tools may still be open from an earlier run.  On Windows,
    locked files sometimes survive a single delete attempt.  Retrying and then
    reporting leftovers prevents the one-click workflow from waiting on stale
    build outputs.
    """
    path.mkdir(parents=True, exist_ok=True)
    keep_names = keep_names or set()
    for _ in range(max(1, retries)):
        _clean_directory(path, keep_names=keep_names)
        leftovers = [p for p in path.iterdir() if p.name not in keep_names]
        if not leftovers:
            return []
        time.sleep(delay_sec)
    return [p for p in path.iterdir() if p.name not in keep_names]


def _wait_until_stable(
    path: Path,
    patterns: tuple[str, ...],
    *,
    min_count: int = 1,
    timeout_sec: int = 600,
    stable_sec: float = 3.0,
    poll_sec: float = 1.0,
    expected_names: set[str] | None = None,
    proc: subprocess.Popen | None = None,
    baseline_snapshot: dict[str, tuple[int, int]] | None = None,
    require_changed: bool = False,
    allow_any_changed_bank: bool = False,
) -> tuple[bool, str]:
    """Wait until a directory has enough matching files and file sizes stop changing.

    ``require_changed`` prevents false positives from old files that were left in
    the Fmod Bank Tools build directory.  This is important for repeated one-click
    runs with the same bank name: the directory may already contain a bank from a
    previous run, but the current Rebuild has not produced a fresh output yet.
    """
    start = time.time()
    stable_since: float | None = None
    last_sig: tuple[int, int] | None = None
    expected_names = {n.lower() for n in expected_names or set()}
    missing: list[str] = []
    stale_expected: list[str] = []

    while time.time() - start < timeout_sec:
        found_files = _matching_files(path, patterns, recursive=False)
        found = {p.name.lower() for p in found_files}
        changed = _files_changed_since_baseline(path, patterns, baseline_snapshot)

        if expected_names:
            missing = sorted(expected_names - found)
            stale_expected = sorted(n for n in expected_names if n in found and require_changed and n not in changed)
            enough = not missing and not stale_expected
            # Fallback: some Fmod Bank Tools variants rebuild a valid bank but the
            # name may differ from the input bank txt naming.  Do not hang forever
            # if there is a fresh stable bank output; the deploy step will still
            # verify exact names before overwriting the game.
            if not enough and allow_any_changed_bank and changed:
                enough = True
        else:
            count = len(found_files)
            enough = count >= min_count
            if require_changed:
                enough = enough and bool(changed)

        sig = _dir_signature(path, patterns)
        if enough and sig == last_sig:
            if stable_since is None:
                stable_since = time.time()
            if time.time() - stable_since >= stable_sec:
                detail = f"目录输出已稳定：{path}，文件数={sig[0]}，总大小={sig[1]} bytes。"
                if changed:
                    detail += " 本次更新文件：" + ", ".join(sorted(changed)) + "。"
                if expected_names and not expected_names.issubset(found):
                    detail += " 注意：期望文件名未全部出现，后续部署会继续做严格校验。"
                return True, detail
        else:
            stable_since = None
            last_sig = sig

        # Check external process after checking outputs.  Some users close the
        # Fmod Bank Tools window immediately after it says Rebuild finished.  If
        # the output is already stable, the success path above should win.
        if proc is not None and proc.poll() is not None:
            if found_files and (not require_changed or changed):
                # Give the filesystem one last chance to settle before deciding.
                time.sleep(min(1.0, poll_sec))
                sig2 = _dir_signature(path, patterns)
                if sig2 == sig:
                    return True, f"外部 Fmod Bank Tools 已退出，但输出文件已稳定：{path}，文件数={sig2[0]}，总大小={sig2[1]} bytes。"
            return False, f"外部 Fmod Bank Tools 已关闭或退出，退出码={proc.returncode}。已安全中止等待。"
        time.sleep(poll_sec)

    if expected_names:
        parts = []
        if missing:
            parts.append("缺少：" + ", ".join(missing))
        if stale_expected:
            parts.append("疑似旧文件未更新：" + ", ".join(stale_expected))
        if not parts:
            parts.append("文件未稳定")
        return False, f"等待超时：{path} 未生成本次 Rebuild 的期望输出（{'；'.join(parts)}）。"
    sig = _dir_signature(path, patterns)
    return False, f"等待超时：{path} 输出未稳定、文件数不足，或仅发现旧文件。当前文件数={sig[0]}。"


def _extract_tree_signature(wav_dir: Path) -> tuple[int, int, int]:
    """Return (txt_count, wav_count, total_size) for Extract output tree.

    Fmod Bank Tools usually writes outputs under nested folders such as
    wav/R6_Tracks_CU1.assets[0]/sound_0.wav and may place the txt list either
    at the wav root or inside a subfolder depending on version/configuration.
    Older FH6 Radio Tool builds only checked the wav root, so a successful
    Extract could still be reported as timeout.
    """
    wav_dir = Path(wav_dir)
    txt_files = [p for p in wav_dir.rglob("*.txt") if p.is_file()] if wav_dir.exists() else []
    wav_files = [p for p in wav_dir.rglob("*.wav") if p.is_file()] + [p for p in wav_dir.rglob("*.WAV") if p.is_file()] if wav_dir.exists() else []
    total = 0
    for f in txt_files + wav_files:
        try:
            total += int(f.stat().st_size)
        except OSError:
            pass
    return len(set(txt_files)), len(set(wav_files)), total


def wait_for_extract_outputs(layout: FmodToolLayout, *, timeout_sec: int = 900, proc: subprocess.Popen | None = None) -> tuple[bool, str]:
    """Wait for Fmod Bank Tools Extract outputs in wav directory.

    The external tool writes wav/txt files recursively under its wav directory.
    A valid Extract is considered complete when at least one txt list and one
    wav file exist somewhere in the output tree and the tree signature remains
    stable for a few seconds.  This fixes the v2.7.3 issue where Extract had
    finished in Fmod Bank Tools but FH6 Radio Tool kept waiting because it only
    looked at the root wav directory.
    """
    start = time.time()
    stable_since: float | None = None
    last_sig: tuple[int, int, int] | None = None
    last_nonzero_sig: tuple[int, int, int] = (0, 0, 0)

    while time.time() - start < timeout_sec:
        sig = _extract_tree_signature(layout.wav_dir)
        txt_count, wav_count, total_size = sig
        if txt_count or wav_count or total_size:
            last_nonzero_sig = sig

        enough = txt_count >= 1 and wav_count >= 1
        if enough and sig == last_sig:
            if stable_since is None:
                stable_since = time.time()
            if time.time() - stable_since >= 10.0:
                return True, (
                    f"Extract 输出已就绪：{layout.wav_dir}，"
                    f"txt={txt_count}，wav={wav_count}，总大小={total_size} bytes。"
                )
        else:
            stable_since = None
            last_sig = sig

        if proc is not None and proc.poll() is not None:
            # Check outputs one last time after the GUI exits.  Users sometimes
            # close Fmod Bank Tools immediately after the console says finished.
            time.sleep(1.0)
            sig2 = _extract_tree_signature(layout.wav_dir)
            txt2, wav2, size2 = sig2
            if txt2 >= 1 and wav2 >= 1:
                return True, (
                    f"Fmod Bank Tools 已退出，但 Extract 输出已存在：{layout.wav_dir}，"
                    f"txt={txt2}，wav={wav2}，总大小={size2} bytes。"
                )
            return False, (
                f"外部 Fmod Bank Tools 已关闭或退出，退出码={proc.returncode}。"
                f"当前 Extract 输出不完整：txt={txt2}，wav={wav2}，总大小={size2} bytes。"
            )

        time.sleep(1.0)

    txt_count, wav_count, total_size = last_nonzero_sig
    return False, (
        f"等待超时：{layout.wav_dir} 没有检测到完整 Extract 输出。"
        f"当前递归扫描结果：txt={txt_count}，wav={wav_count}，总大小={total_size} bytes。"
        "如果 Fmod Bank Tools 已显示 Extracting Bank files has finished，请确认 wav 目录下是否生成了子文件夹、txt 清单和 sound_*.wav。"
    )


def wait_for_rebuild_outputs(
    layout: FmodToolLayout,
    expected_bank_names: Iterable[str] | None = None,
    *,
    timeout_sec: int = 1200,
    proc: subprocess.Popen | None = None,
    baseline_snapshot: dict[str, tuple[int, int]] | None = None,
) -> tuple[bool, str]:
    names = {Path(x).name for x in (expected_bank_names or []) if str(x).strip()}
    if names:
        return _wait_until_stable(
            layout.rebuild_dir,
            ("*.bank",),
            timeout_sec=timeout_sec,
            stable_sec=4.0,
            expected_names=names,
            proc=proc,
            baseline_snapshot=baseline_snapshot,
            require_changed=True,
            allow_any_changed_bank=True,
        )
    return _wait_until_stable(
        layout.rebuild_dir,
        ("*.bank",),
        min_count=1,
        timeout_sec=timeout_sec,
        stable_sec=4.0,
        proc=proc,
        baseline_snapshot=baseline_snapshot,
        require_changed=True,
    )


def launch_trigger_and_wait(exe_path: Path, action: str, *, auto_trigger: bool = True, expected_bank_names: Iterable[str] | None = None, timeout_sec: int | None = None) -> FmodAutomationResult:
    """Launch Fmod Bank Tools, trigger an action, wait for outputs, then close it.

    The external GUI is treated as a short-lived worker.  This prevents developer
    scans from leaving hundreds of Fmod Bank Tools windows open and also ensures
    every subsequent Extract/Rebuild starts from a fresh process state.
    """
    layout = layout_from_exe(exe_path)
    action_lower = action.lower().strip()
    proc: subprocess.Popen | None = None
    try:
        if action_lower == "extract":
            prepared_banks = sorted(layout.bank_dir.glob("*.bank")) if layout.bank_dir.exists() else []
            if not prepared_banks:
                return FmodAutomationResult(
                    False,
                    action,
                    f"Fmod Bank Tools 的 bank 目录为空：{layout.bank_dir}。已停止启动 Extract，避免外部工具提示 No bank files found。",
                    layout,
                    None,
                    None,
                    None,
                )

        rebuild_baseline = _file_snapshot(layout.rebuild_dir, ("*.bank",), recursive=False) if action_lower == "rebuild" else None
        proc = launch_tool(layout)
        message = f"已启动 Fmod Bank Tools，PID={proc.pid}。"
        if action_lower == "rebuild":
            message += f" Rebuild 基线文件数={len(rebuild_baseline or {})}。"
        trigger_ok = True
        if auto_trigger:
            trigger_ok, trig_msg = try_trigger_gui_action(proc.pid, action)
            message += " " + trig_msg
            if not trigger_ok:
                return FmodAutomationResult(
                    False,
                    action,
                    message + " 一键流程需要自动点击成功；" + pywinauto_user_hint(),
                    layout,
                    None,
                    proc.pid,
                    None,
                )
        else:
            return FmodAutomationResult(
                False,
                action,
                message + " 内部自动触发未启用，因此已停止等待；请使用 v3.0.37 或更新版本重新构建。" + pywinauto_user_hint(),
                layout,
                None,
                proc.pid,
                None,
            )

        if action_lower == "extract":
            wait_ok, wait_msg = wait_for_extract_outputs(layout, timeout_sec=timeout_sec or 900, proc=proc)
        elif action_lower == "rebuild":
            wait_ok, wait_msg = wait_for_rebuild_outputs(layout, expected_bank_names, timeout_sec=timeout_sec or 1200, proc=proc, baseline_snapshot=rebuild_baseline)
        else:
            return FmodAutomationResult(False, action, message + f" 未知 action: {action}", layout, None, proc.pid if proc else None, None)

        ok = bool(wait_ok)
        return FmodAutomationResult(ok, action, message + " " + wait_msg + " 已关闭本次 Fmod Bank Tools 进程。", layout, None, proc.pid if proc else None, None)
    finally:
        _terminate_process(proc)
