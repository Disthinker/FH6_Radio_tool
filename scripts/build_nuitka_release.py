from __future__ import annotations

import base64
import compileall
import importlib.util
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIST_ROOT = PROJECT_ROOT / "dist_release"
NUITKA_WORK = PROJECT_ROOT / "build_nuitka"
NUITKA_OUT = PROJECT_ROOT / "dist_nuitka"
ENTRY = PROJECT_ROOT / "build_nuitka_entry.py"
APP_ICON = NUITKA_WORK / "app.ico"
APP_ICON_B64 = PROJECT_ROOT / "resources" / "app_icon_base64.txt"
ARCHIVE_EXTENSIONS = {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".tbz", ".xz", ".cab", ".iso"}
LOOPFINDER_DLL_NAME = "loopfinder.dll" if os.name == "nt" else "libloopfinder.so"

# v3.0.38: automatic Fmod Bank Tools control can use either pywinauto
# or the built-in ctypes/Win32 fallback.  Do not fail the whole build only
# because pywinauto/comtypes has a stale typelib cache.
REQUIRED_WINDOWS_AUTOMATION_IMPORTS = [
    "pywinauto",
    "pywinauto.application",
    "pywinauto.keyboard",
    "comtypes",
    "comtypes.client",
    "pythoncom",
    "pywintypes",
    "win32api",
    "win32con",
    "win32gui",
    "win32process",
]

NUITKA_INCLUDE_PACKAGES = [
    "pywinauto",
    "comtypes",
    "win32com",
    "win32comext",
]

NUITKA_INCLUDE_MODULES = [
    "six",
    "pythoncom",
    "pywintypes",
    "win32api",
    "win32con",
    "win32gui",
    "win32process",
    "pywinauto.application",
    "pywinauto.keyboard",
    "pywinauto.controls.uiawrapper",
]

REQUIRED_QT_MODULES = [
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtNetwork",
]


def log(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    log("      " + " ".join(str(x) for x in cmd))
    proc = subprocess.run(cmd, cwd=str(cwd or PROJECT_ROOT), env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {proc.returncode}: {' '.join(cmd)}")


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


def ensure_build_icon() -> None:
    """Create a temporary ICO for Nuitka embedding.

    The final Nexus upload package intentionally does not include loose .ico files.
    The icon is embedded into FH6RadioTool.exe during the Windows Nuitka build.
    """
    APP_ICON.parent.mkdir(parents=True, exist_ok=True)
    loose_icon = PROJECT_ROOT / "resources" / "app.ico"
    if loose_icon.exists():
        shutil.copy2(loose_icon, APP_ICON)
        return
    if APP_ICON_B64.exists():
        APP_ICON.write_bytes(base64.b64decode(APP_ICON_B64.read_text(encoding="ascii")))
        return
    raise RuntimeError("Missing application icon source: resources/app.ico or resources/app_icon_base64.txt")


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
        log("      [WARN] imageio-ffmpeg binary not found; the app will rely on PATH or user settings.")


def write_exe_readme(bundle_dir: Path, version: str) -> None:
    text = f"""FH6 Radio Tool v{version} - Nuitka Onefile Portable Package

中文说明：
1. 双击 FH6RadioTool.exe 启动工具，不需要先运行 setup_env.bat。
2. Fmod Bank Tools 不会被内置；请在工具设置里选择你自己的 Fmod_Bank_Tools.exe。
3. output、backup、work 会生成在 FH6RadioTool.exe 同目录。
4. 本包不包含 loose .ico、游戏文件、音乐文件、bank 文件或源码目录。
5. 本 EXE 已内置自动控制依赖；用户不需要、也不能通过旧版 setup_env.bat 修复此 EXE。
6. 如果自动控制仍提示缺失，请下载 v3.0.38 或更新版本重新打包。

English:
1. Double-click FH6RadioTool.exe to start. Python setup is not required.
2. Fmod Bank Tools is not bundled; select your own Fmod_Bank_Tools.exe in settings.
3. output, backup and work are created next to FH6RadioTool.exe.
4. This package contains no loose .ico files, game files, music files, bank files or source folder.
5. This EXE should include the automation dependencies; users do not need and cannot repair it with an old setup_env.bat.
6. If automation is still reported missing, rebuild/download v3.0.38 or newer.
"""
    (bundle_dir / "README_NUITKA_PORTABLE.txt").write_text(text, encoding="utf-8")


def make_zip(src_dir: Path, zip_path: Path, top_name: str) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in src_dir.rglob("*"):
            if p.is_file():
                arc = Path(top_name) / p.relative_to(src_dir)
                zf.write(p, arc.as_posix())


def find_nested_archives(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in ARCHIVE_EXTENSIONS]


def verify_nexus_package_dir(package_dir: Path) -> None:
    exe = package_dir / "FH6RadioTool.exe"
    if not exe.exists():
        raise RuntimeError(f"EXE not found: {exe}")
    nested = find_nested_archives(package_dir)
    if nested:
        rels = [str(p.relative_to(package_dir)) for p in nested[:20]]
        raise RuntimeError("Nexus-safe package contains nested archive files: " + ", ".join(rels))
    forbidden_dirs = ["fh6_radio_tool", "build_nuitka", "dist_nuitka", "__pycache__"]
    for name in forbidden_dirs:
        if (package_dir / name).exists():
            raise RuntimeError(f"Invalid EXE package: {name} should not be copied into package root.")
    loose_ico = list(package_dir.rglob("*.ico"))
    if loose_ico:
        rels = [str(p.relative_to(package_dir)) for p in loose_ico[:20]]
        raise RuntimeError("Invalid EXE package: loose .ico file(s) found: " + ", ".join(rels))


def importable(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def add_module_if_importable(cmd: list[str], module: str) -> None:
    if importable(module):
        cmd.append(f"--include-module={module}")


def add_package_if_importable(cmd: list[str], package: str) -> None:
    if importable(package):
        cmd.append(f"--include-package={package}")


def verify_windows_automation_imports() -> None:
    """Check automation support without requiring a healthy pywinauto/comtypes import.

    v3.0.38 can use an internal ctypes/Win32 fallback when pywinauto fails with
    stale comtypes errors such as "Typelib different than module". Therefore the
    build should not be blocked solely because pywinauto import is broken.
    """
    code = (
        "from fh6_radio_tool.fmod_automation import pywinauto_status; "
        "ok, detail = pywinauto_status(); "
        "print('automation status:', ok, detail); "
        "raise SystemExit(0 if ok else 1)"
    )
    run([sys.executable, "-c", code])


def main() -> int:
    os.chdir(PROJECT_ROOT)
    log("[1/11] Reading version ...")
    version = read_version()
    log(f"      Version: {version}")

    if os.name != "nt":
        raise RuntimeError(
            "Windows EXE packaging must be run on Windows. "
            "Nuitka cannot cross-compile a Windows .exe from Linux/WSL in this project."
        )

    log("[2/11] Checking Windows automation dependencies ...")
    verify_windows_automation_imports()

    log("[3/11] Checking entry files ...")
    if not ENTRY.exists():
        ENTRY.write_text('from fh6_radio_tool.v2_ui import main\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n', encoding="utf-8")
    for rel in ["fh6_radio_tool/app.py", "fh6_radio_tool/v2_ui.py", "requirements.txt"]:
        if not (PROJECT_ROOT / rel).exists():
            raise RuntimeError(f"Missing required file: {rel}")
    ensure_build_icon()

    log("[4/11] Python syntax check ...")
    ok = compileall.compile_dir(str(PROJECT_ROOT / "fh6_radio_tool"), quiet=1, force=False)
    ok = bool(ok and compileall.compile_file(str(ENTRY), quiet=1, force=False))
    remove_py_caches(PROJECT_ROOT / "fh6_radio_tool")
    if not ok:
        raise RuntimeError("compileall failed")

    log("[5/11] Preparing optional native LoopFinder DLL ...")
    loopfinder_dll = ensure_loopfinder_dll_for_build()
    if loopfinder_dll:
        log(f"      LoopFinder DLL: {loopfinder_dll}")
    else:
        log("      [WARN] LoopFinder DLL not available; Analyze Loop will fall back to Python engines.")

    log("[6/11] Cleaning previous Nuitka outputs ...")
    shutil.rmtree(NUITKA_WORK, ignore_errors=True)
    shutil.rmtree(NUITKA_OUT, ignore_errors=True)
    DIST_ROOT.mkdir(parents=True, exist_ok=True)
    ensure_build_icon()

    log("[7/11] Running Nuitka Windows onefile build ...")
    py = sys.executable
    cmd = [
        py,
        "-m",
        "nuitka",
        "--standalone",
        "--onefile",
        "--assume-yes-for-downloads",
        "--enable-plugin=pyside6",
        "--lto=no",
        "--jobs=2",
        "--windows-console-mode=disable",
        f"--windows-icon-from-ico={APP_ICON}",
        f"--include-data-dir={PROJECT_ROOT / 'docs'}=docs",
        f"--include-data-dir={PROJECT_ROOT / 'config'}=config",
        f"--include-data-dir={PROJECT_ROOT / 'third_party_licenses'}=third_party_licenses",
        "--include-package-data=imageio_ffmpeg",
        "--include-module=fh6_radio_tool.loopfinder_worker",
        "--include-module=fh6_radio_tool.loop_engine.seamless_loopfinder",
        f"--output-dir={NUITKA_OUT}",
        "--output-filename=FH6RadioTool.exe",
    ]
    if loopfinder_dll and loopfinder_dll.exists():
        cmd.append(f"--include-data-file={loopfinder_dll}=loopfinder.dll")
    for module in REQUIRED_QT_MODULES:
        cmd.append(f"--include-module={module}")
    for package in NUITKA_INCLUDE_PACKAGES:
        add_package_if_importable(cmd, package)
    for module in NUITKA_INCLUDE_MODULES:
        add_module_if_importable(cmd, module)
    cmd.append(str(ENTRY))

    env = os.environ.copy()
    # Release builds can remove these two variables. Keeping them here makes local
    # test builds much faster and avoids gcc/msvc spending excessive time on Qt-heavy C output.
    env.setdefault("CFLAGS", "-O0")
    env.setdefault("CXXFLAGS", "-O0")
    run(cmd, env=env)

    onefile_exe = NUITKA_OUT / "FH6RadioTool.exe"
    if not onefile_exe.exists():
        # Nuitka may put onefile output next to the entry base name if output filename
        # semantics differ across versions; fail loudly rather than guessing.
        raise RuntimeError(f"Nuitka onefile output not found: {onefile_exe}")

    log("[8/11] Creating Nexus-safe portable folder without loose ICO/source files ...")
    package_name = f"FH6_Radio_Tool_v{version}_nexus_nuitka_onefile"
    stage_root = DIST_ROOT / "_nuitka_stage"
    package_dir = stage_root / package_name
    shutil.rmtree(stage_root, ignore_errors=True)
    package_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(onefile_exe, package_dir / "FH6RadioTool.exe")
    # v3.0.38: do not bundle a separate ffmpeg.exe in the Nexus package.
    # The app can use imageio-ffmpeg from the bundled Python package or a user-provided PATH/tool.
    if (PROJECT_ROOT / "config").exists():
        shutil.copytree(PROJECT_ROOT / "config", package_dir / "config")
    write_exe_readme(package_dir, version)

    log("[9/11] Verifying Nexus-safe package folder ...")
    verify_nexus_package_dir(package_dir)

    log("[10/11] Creating Nexus-safe Nuitka ZIP ...")
    zip_path = DIST_ROOT / f"{package_name}.zip"
    make_zip(package_dir, zip_path, package_name)

    log("[11/11] Verifying ZIP ...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        if not any(n.endswith("FH6RadioTool.exe") for n in names):
            raise RuntimeError("ZIP verification failed: FH6RadioTool.exe missing")
        if any(Path(n).suffix.lower() in ARCHIVE_EXTENSIONS for n in names):
            raise RuntimeError("ZIP verification failed: nested archive files found")
        if any(n.lower().endswith(".ico") for n in names):
            raise RuntimeError("ZIP verification failed: loose .ico file found")
        if any("fh6_radio_tool/" in n for n in names):
            raise RuntimeError("ZIP verification failed: source package folder found")
        log(f"      Files in ZIP: {sum(1 for n in names if not n.endswith('/'))}")

    log("[OK] Done.")
    log("")
    log("[OK] Nexus-safe Nuitka onefile portable package created:")
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
