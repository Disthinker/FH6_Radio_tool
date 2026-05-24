from __future__ import annotations

import compileall
import fnmatch
import os
import shutil
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIST_ROOT = PROJECT_ROOT / "dist_release"

EXCLUDED_DIR_NAMES = {
    ".git", ".idea", ".vscode", ".venv",
    "__pycache__", ".pytest_cache", ".mypy_cache",
    "work", "output", "backup",
    "dist_release", "dist_nuitka_debug", "dist_nuitka_release",
    "build", "build_nuitka_entry.build", "build_nuitka_entry.dist",
}

EXCLUDED_FILE_PATTERNS = {
    "*.pyc", "*.pyo", "*.log", "*.tmp", "*.bak", "*.bak_*",
    "*.zip", "*.7z", "*.rar",
    "fh6_radio_tool_v2.sqlite3", "fh6_radio_tool_v2.sqlite3-*",
    "pywinauto_probe.py", "patch_*.py",
}

REQUIRED_FILES = [
    "setup_env.bat",
    "run_tool.bat",
    "cleanup_env.bat",
    "requirements.txt",
    "fh6_radio_tool/app.py",
    "fh6_radio_tool/v2_ui.py",
]


def log(message: str) -> None:
    print(message, flush=True)


def read_version() -> str:
    sys.path.insert(0, str(PROJECT_ROOT))
    from fh6_radio_tool.v2_state_store import APP_VERSION  # type: ignore
    import fh6_radio_tool  # type: ignore
    init_version = getattr(fh6_radio_tool, "__version__", None)
    if init_version and init_version != APP_VERSION:
        raise RuntimeError(f"Version mismatch: APP_VERSION={APP_VERSION}, __version__={init_version}")
    return APP_VERSION


def should_skip_dir(path: Path) -> bool:
    return any(part in EXCLUDED_DIR_NAMES for part in path.relative_to(PROJECT_ROOT).parts)


def should_skip_file(path: Path) -> bool:
    rel = path.relative_to(PROJECT_ROOT)
    if any(part in EXCLUDED_DIR_NAMES for part in rel.parts[:-1]):
        return True
    name = path.name
    if name.startswith("#U"):
        raise RuntimeError(f"Suspicious #Uxxxx filename found: {path}")
    return any(fnmatch.fnmatch(name, pat) for pat in EXCLUDED_FILE_PATTERNS)


def remove_py_caches(root: Path) -> None:
    for d in list(root.rglob("__pycache__")):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
    for pattern in ("*.pyc", "*.pyo", "*.log"):
        for p in root.rglob(pattern):
            if p.is_file():
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass


def copy_release_tree(stage_dir: Path) -> int:
    copied = 0
    for src in PROJECT_ROOT.rglob("*"):
        if src == stage_dir or stage_dir in src.parents:
            continue
        rel = src.relative_to(PROJECT_ROOT)
        if src.is_dir():
            if should_skip_dir(src):
                continue
            (stage_dir / rel).mkdir(parents=True, exist_ok=True)
            continue
        if should_skip_file(src):
            continue
        dst = stage_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    return copied


def make_zip(stage_dir: Path, zip_path: Path, package_name: str) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in stage_dir.rglob("*"):
            if p.is_file():
                arcname = Path(package_name) / p.relative_to(stage_dir)
                zf.write(p, arcname.as_posix())


def verify_zip(zip_path: Path) -> tuple[int, int]:
    file_count = 0
    dir_count = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        for name in names:
            if name.endswith("/"):
                dir_count += 1
            else:
                file_count += 1
            if "/#U" in name or Path(name).name.startswith("#U"):
                raise RuntimeError(f"Suspicious #Uxxxx filename found inside zip: {name}")
        required_suffixes = [
            "setup_env.bat",
            "run_tool.bat",
            "fh6_radio_tool/app.py",
            "fh6_radio_tool/v2_ui.py",
        ]
        missing = [s for s in required_suffixes if not any(n.endswith(s) for n in names)]
        if missing:
            raise RuntimeError("ZIP is missing required files: " + ", ".join(missing))
    return file_count, dir_count


def main() -> int:
    os.chdir(PROJECT_ROOT)
    log("[1/8] Reading version ...")
    version = read_version()
    package_name = f"FH6_Radio_Tool_v{version}_portable_batch"
    stage_dir = DIST_ROOT / "_stage" / package_name
    zip_path = DIST_ROOT / f"{package_name}.zip"

    log(f"      Version: {version}")

    log("[2/8] Checking required files ...")
    missing = [name for name in REQUIRED_FILES if not (PROJECT_ROOT / name).exists()]
    if missing:
        raise RuntimeError("Missing required files: " + ", ".join(missing))

    log("[3/8] Python syntax check ...")
    ok = compileall.compile_dir(str(PROJECT_ROOT / "fh6_radio_tool"), quiet=1, force=False)
    remove_py_caches(PROJECT_ROOT / "fh6_radio_tool")
    if not ok:
        raise RuntimeError("compileall failed")

    log("[4/8] Cleaning old release output ...")
    if stage_dir.parent.exists():
        shutil.rmtree(stage_dir.parent, ignore_errors=True)
    if zip_path.exists():
        zip_path.unlink()
    DIST_ROOT.mkdir(parents=True, exist_ok=True)
    stage_dir.mkdir(parents=True, exist_ok=True)

    log("[5/8] Copying actual release files ...")
    copied = copy_release_tree(stage_dir)
    log(f"      Copied files: {copied}")
    if copied < 20:
        raise RuntimeError(f"Too few files copied ({copied}). Refusing to create an empty/invalid package.")

    log("[6/8] Cleaning package caches/runtime files ...")
    remove_py_caches(stage_dir)

    log("[7/8] Creating ZIP ...")
    make_zip(stage_dir, zip_path, package_name)

    log("[8/8] Verifying ZIP contents ...")
    file_count, dir_count = verify_zip(zip_path)
    if file_count < 20:
        raise RuntimeError(f"ZIP contains too few files ({file_count}). Package is invalid.")

    log("")
    log("[OK] Portable batch release ZIP created:")
    log(f"     {zip_path}")
    log(f"     Files in ZIP: {file_count}, directory entries: {dir_count}")
    log("")
    log("Test by extracting the ZIP to a clean folder, then run:")
    log("     setup_env.bat")
    log("     run_tool.bat")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("")
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
