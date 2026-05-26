from __future__ import annotations

import compileall
import os
import base64
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYINSTALLER_EXCLUDE_QT_MODULES = [
    # The app uses QtCore/QtGui/QtWidgets/QtMultimedia only.
    # Do not collect QML/Quick/Charts: PyInstaller may scan missing QML
    # plugin DLLs and produce noisy logging errors or very slow builds.
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
]
PYINSTALLER_REQUIRED_QT_IMPORTS = [
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtNetwork",
]
DIST_ROOT = PROJECT_ROOT / "dist_release"
PYINSTALLER_WORK = PROJECT_ROOT / "build_pyinstaller"
PYINSTALLER_DIST = PROJECT_ROOT / "dist_pyinstaller"
ENTRY = PROJECT_ROOT / "build_pyinstaller_entry.py"
APP_ICON = PYINSTALLER_WORK / "app.ico"
APP_ICON_B64 = PROJECT_ROOT / "resources" / "app_icon_base64.txt"
ARCHIVE_EXTENSIONS = {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".tbz", ".xz", ".cab", ".iso"}
LOOPFINDER_DLL_NAME = "loopfinder.dll" if os.name == "nt" else "libloopfinder.so"


def log(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: list[str], *, cwd: Path | None = None) -> None:
    log("      " + " ".join(str(x) for x in cmd))
    proc = subprocess.run(cmd, cwd=str(cwd or PROJECT_ROOT))
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {proc.returncode}: {' '.join(cmd)}")


def read_version() -> str:
    sys.path.insert(0, str(PROJECT_ROOT))
    from fh6_radio_tool.v2_state_store import APP_VERSION  # type: ignore
    import fh6_radio_tool  # type: ignore
    init_version = getattr(fh6_radio_tool, "__version__", None)
    if init_version and init_version != APP_VERSION:
        raise RuntimeError(f"Version mismatch: APP_VERSION={APP_VERSION}, __version__={init_version}")
    return APP_VERSION


def remove_py_caches(root: Path) -> None:
    for d in list(root.rglob("__pycache__")):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
    for pat in ("*.pyc", "*.pyo", "*.log"):
        for p in root.rglob(pat):
            if p.is_file():
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass


def data_arg(src: str, dst: str) -> str:
    sep = ";" if os.name == "nt" else ":"
    return f"{src}{sep}{dst}"


def find_loopfinder_dll_for_build() -> Path | None:
    candidates = [
        PROJECT_ROOT / "tools" / LOOPFINDER_DLL_NAME,
        PROJECT_ROOT / "build_loopfinder" / "Release" / LOOPFINDER_DLL_NAME,
        PROJECT_ROOT / "build_loopfinder" / LOOPFINDER_DLL_NAME,
        PROJECT_ROOT / "third_party" / "loopfinder" / "build" / "Release" / LOOPFINDER_DLL_NAME,
        PROJECT_ROOT / "third_party" / "loopfinder" / "build" / LOOPFINDER_DLL_NAME,
    ]
    for p in candidates:
        if p.exists() and p.is_file():
            return p
    return None


def ensure_loopfinder_dll_for_build() -> Path | None:
    dll = find_loopfinder_dll_for_build()
    if dll is not None:
        return dll
    script = PROJECT_ROOT / "scripts" / "build_loopfinder_native.py"
    if not script.exists() or not (PROJECT_ROOT / "third_party" / "loopfinder").exists():
        return None
    proc = subprocess.run([sys.executable, str(script), "--allow-missing"], cwd=str(PROJECT_ROOT))
    if proc.returncode != 0:
        return None
    return find_loopfinder_dll_for_build()


def ensure_build_icon() -> bool:
    """Create a temporary ICO only for PyInstaller build.

    v3.0.38 fix: the previous script created build_pyinstaller/app.ico
    before deleting build_pyinstaller during the clean step, so PyInstaller
    failed at "Copying icon to EXE" with FileNotFoundError.  This helper is
    now called after cleaning and returns False instead of failing the build
    when no icon source is available.  Functionality is more important than
    a custom icon.
    """
    APP_ICON.parent.mkdir(parents=True, exist_ok=True)
    loose_icon = PROJECT_ROOT / "resources" / "app.ico"
    try:
        if loose_icon.exists():
            shutil.copy2(loose_icon, APP_ICON)
            return True
        if APP_ICON_B64.exists():
            data = base64.b64decode(APP_ICON_B64.read_text(encoding="ascii"))
            APP_ICON.write_bytes(data)
            return True
    except Exception as exc:
        log(f"      [WARN] Could not create build icon, continuing without custom icon: {exc}")
        return False
    log("      [WARN] No application icon source found; building without custom icon.")
    return False


def find_imageio_ffmpeg() -> Path | None:
    try:
        import imageio_ffmpeg  # type: ignore
        p = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if p.exists():
            return p
    except Exception:
        return None
    return None


def copy_ffmpeg_to_bundle(bundle_dir: Path) -> None:
    ffmpeg = find_imageio_ffmpeg()
    tools_dir = bundle_dir / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    if ffmpeg and ffmpeg.exists():
        target_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        shutil.copy2(ffmpeg, tools_dir / target_name)
        log(f"      Bundled FFmpeg: {tools_dir / target_name}")
    else:
        log("      [WARN] imageio-ffmpeg ffmpeg binary not found; package will rely on PATH or user setting.")


def write_exe_readme(bundle_dir: Path, version: str) -> None:
    text = f"""FH6 Radio Tool v{version} - EXE Portable Package

中文说明：
1. 双击 FH6RadioTool.exe 启动工具，不需要先运行 setup_env.bat。
2. Fmod Bank Tools 仍然不会内置；请在工具设置里选择你自己的 Fmod_Bank_Tools.exe。
3. output、backup、work 会生成在 FH6RadioTool.exe 同目录。
4. v3.0.39 已内置 Win32 fallback 和开发者模式；如果 pywinauto/comtypes 异常，仍会尝试自动控制 Fmod Bank Tools。
5. 如果 EXE 版异常，仍可回到源码包使用 setup_env.bat + run_tool.bat。

English:
1. Double-click FH6RadioTool.exe to start. Python setup is not required.
2. Fmod Bank Tools is still external; select your own Fmod_Bank_Tools.exe in settings.
3. output, backup and work are created next to FH6RadioTool.exe.
4. v3.0.39 includes a Win32 fallback and developer mode; if pywinauto/comtypes is broken, it can still try to control Fmod Bank Tools.
5. If this EXE build has issues, use the source/batch package as fallback.
"""
    (bundle_dir / "README_EXE_PORTABLE.txt").write_text(text, encoding="utf-8")


def make_zip(src_dir: Path, zip_path: Path, top_name: str) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in src_dir.rglob("*"):
            if p.is_file():
                arc = Path(top_name) / p.relative_to(src_dir)
                zf.write(p, arc.as_posix())


def find_nested_archives(root: Path) -> list[Path]:
    found: list[Path] = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in ARCHIVE_EXTENSIONS:
            found.append(p)
    return found


def verify_nexus_package_dir(package_dir: Path) -> None:
    exe = package_dir / "FH6RadioTool.exe"
    if os.name == "nt" and not exe.exists():
        raise RuntimeError(f"EXE not found: {exe}")
    nested = find_nested_archives(package_dir)
    if nested:
        rels = [str(p.relative_to(package_dir)) for p in nested[:20]]
        raise RuntimeError("Nexus-safe package contains nested archive files: " + ", ".join(rels))
    if (package_dir / "fh6_radio_tool").exists():
        raise RuntimeError("Invalid EXE package: source folder fh6_radio_tool was copied into package root.")


def main() -> int:
    os.chdir(PROJECT_ROOT)
    log("[1/10] Reading version ...")
    version = read_version()
    log(f"      Version: {version}")

    if os.name != "nt":
        raise RuntimeError("EXE packaging must be run on Windows. This script prepares a Windows PyInstaller build.")

    log("[2/10] Checking entry files ...")
    if not ENTRY.exists():
        ENTRY.write_text('from fh6_radio_tool.v2_ui import main\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n', encoding="utf-8")
    for rel in ["fh6_radio_tool/app.py", "fh6_radio_tool/v2_ui.py", "requirements.txt"]:
        if not (PROJECT_ROOT / rel).exists():
            raise RuntimeError(f"Missing required file: {rel}")

    log("[3/10] Python syntax check ...")
    ok = compileall.compile_dir(str(PROJECT_ROOT / "fh6_radio_tool"), quiet=1, force=False)
    ok = bool(ok and compileall.compile_file(str(ENTRY), quiet=1, force=False))
    remove_py_caches(PROJECT_ROOT / "fh6_radio_tool")
    if not ok:
        raise RuntimeError("compileall failed")

    log("[4/10] Preparing optional native LoopFinder DLL ...")
    loopfinder_dll = ensure_loopfinder_dll_for_build()
    if loopfinder_dll:
        log(f"      LoopFinder DLL: {loopfinder_dll}")
    else:
        log("      [WARN] LoopFinder DLL not available; Analyze Loop will fall back to Python engines.")

    log("[5/10] Cleaning previous build outputs ...")
    shutil.rmtree(PYINSTALLER_WORK, ignore_errors=True)
    shutil.rmtree(PYINSTALLER_DIST, ignore_errors=True)
    DIST_ROOT.mkdir(parents=True, exist_ok=True)

    log("[6/10] Running PyInstaller one-file build ...")
    icon_ready = ensure_build_icon()
    py = sys.executable
    cmd = [
        py, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name", "FH6RadioTool",
        "--workpath", str(PYINSTALLER_WORK),
        "--distpath", str(PYINSTALLER_DIST),
    ]
    if icon_ready:
        cmd += ["--icon", str(APP_ICON)]
    cmd += [
        # Intentionally do NOT use "--collect-all PySide6".  It pulls in
        # QML/Quick/Charts/WebEngine modules that FH6 Radio Tool does not use,
        # causing excessive build time and PyInstaller QtQml hook errors on
        # some PySide6 wheels.  PyInstaller's normal PySide6 hooks collect the
        # binaries/plugins for the modules imported by the app.
        "--hidden-import", "imageio_ffmpeg",
        "--hidden-import", "fh6_radio_tool.loopfinder_worker",
        "--hidden-import", "fh6_radio_tool.loop_engine.seamless_loopfinder",
        "--collect-all", "imageio_ffmpeg",
        "--hidden-import", "pywinauto",
        "--hidden-import", "pywinauto.application",
        "--hidden-import", "pywinauto.keyboard",
        "--hidden-import", "pywinauto.controls.uiawrapper",
        "--hidden-import", "comtypes",
        "--hidden-import", "comtypes.client",
        "--hidden-import", "pythoncom",
        "--hidden-import", "pywintypes",
        "--hidden-import", "win32api",
        "--hidden-import", "win32con",
        "--hidden-import", "win32gui",
        "--hidden-import", "win32process",
    ]
    for mod in PYINSTALLER_REQUIRED_QT_IMPORTS:
        cmd += ["--hidden-import", mod]
    for mod in PYINSTALLER_EXCLUDE_QT_MODULES:
        cmd += ["--exclude-module", mod]
    for src, dst in [("docs", "docs"), ("third_party_licenses", "third_party_licenses"), ("config", "config")]:
        if (PROJECT_ROOT / src).exists():
            cmd += ["--add-data", data_arg(str(PROJECT_ROOT / src), dst)]
    if loopfinder_dll and loopfinder_dll.exists():
        cmd += ["--add-binary", data_arg(str(loopfinder_dll), ".")]
    cmd.append(str(ENTRY))
    run(cmd)

    onefile_exe = PYINSTALLER_DIST / "FH6RadioTool.exe"
    if not onefile_exe.exists():
        raise RuntimeError(f"PyInstaller one-file output not found: {onefile_exe}")

    log("[7/10] Creating Nexus-safe portable folder without loose ICO files ...")
    package_name = f"FH6_Radio_Tool_v{version}_nexus_exe_portable"
    stage_root = DIST_ROOT / "_nexus_stage"
    package_dir = stage_root / package_name
    shutil.rmtree(stage_root, ignore_errors=True)
    package_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(onefile_exe, package_dir / "FH6RadioTool.exe")
    # Do not copy a loose tools/ffmpeg.exe into the Nexus upload package.
    # imageio-ffmpeg is collected into the one-file EXE by PyInstaller.
    if (PROJECT_ROOT / "config").exists():
        shutil.copytree(PROJECT_ROOT / "config", package_dir / "config")
    # Do not copy loose .ico files into the final Nexus upload package.
    # The icon has already been embedded into FH6RadioTool.exe by PyInstaller.
    write_exe_readme(package_dir, version)

    log("[8/10] Verifying Nexus-safe package folder ...")
    verify_nexus_package_dir(package_dir)

    log("[9/10] Creating Nexus-safe EXE portable ZIP ...")
    zip_path = DIST_ROOT / f"{package_name}.zip"
    make_zip(package_dir, zip_path, package_name)

    log("[10/10] Verifying ZIP ...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        if not any(n.endswith("FH6RadioTool.exe") for n in names):
            raise RuntimeError("ZIP verification failed: FH6RadioTool.exe missing")
        if any(Path(n).name.startswith("#U") for n in names):
            raise RuntimeError("ZIP verification failed: suspicious #Uxxxx filename found")
        nested = [n for n in names if Path(n).suffix.lower() in ARCHIVE_EXTENSIONS]
        if nested:
            raise RuntimeError("ZIP verification failed: nested archive files found: " + ", ".join(nested[:20]))
        log(f"      Files in ZIP: {sum(1 for n in names if not n.endswith('/'))}")

    log("")
    log("[OK] Nexus-safe EXE portable package created:")
    log(f"     {zip_path}")
    log("")
    log("Test it by extracting the ZIP to a clean folder and double-clicking:")
    log("     FH6RadioTool.exe")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("")
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
