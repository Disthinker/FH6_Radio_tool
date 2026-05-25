## v3.1.3

- Optimized developer-mode XML → bank matching after full-bank Extract.
- Replaced the previous XML-target × FMOD-record full scan with indexed token and length-range matching.
- Added a compact progress message for the new fast matching stage.
- Existing all-bank Extract cache reuse / delete-and-retest prompt remains available.

## v3.1.1

- Release candidate for developer mode and main tool workflow.
- Fixed Fmod Bank Tools CPU thread setting: the user-selected maximum thread count is now written into `config.ini` for Extract and Rebuild jobs instead of always falling back to 2.
- The same thread limit is used by developer-mode precheck/statistics stages and by external Fmod Bank Tools encoding/rebuild stages, while Fmod GUI launches remain serial for stability.

## v3.0.40

- Fix station combo slot-count display so it uses the same visible/replaceable slot filtering as the actual slot table. This fixes cases such as a station label showing 29 slots while the table exposes only 27 replaceable rows.
- Fix developer-mode UI localization. Developer-mode title, thread label, thread hint, buttons, and help text now switch between Chinese and English with the rest of the UI.
- Refresh station combo labels when the UI language changes.


## v3.0.39

- Added an Audio Research Developer Mode.
- Added one-click full-bank extraction/statistics workflow for `media/Audio/FMODBanks`.
- Added `all_bank_extract_statistics.csv` for bank-level audio inventory and extract status.
- Added `xml_to_bank_mapping.csv` for RadioInfo XML to candidate bank/audio mapping analysis.
- Added developer CPU thread limit with local safe-thread recommendation.
- Fmod Bank Tools GUI extraction is still serialized; CPU precheck/statistics/CSV generation are bounded by the selected max thread count.

# FH6 Radio Tool v3.0.38 Fmod Automation Shortcut Hotfix

This build fixes the issue where Fmod Bank Tools opened successfully but Extract/Rebuild was not automatically triggered.

Changes:

- Corrected the fallback shortcuts used for Fmod Bank Tools automation.
- Extract now uses the native Ctrl+E shortcut.
- Rebuild now uses the native Ctrl+B shortcut.
- Removed the older Alt+E / Alt+R assumption from the built-in Win32 fallback path.
- The tool still tries direct pywinauto control first, then falls back to built-in Win32 keyboard triggering.
- PyInstaller remains the recommended build path.

Build on Windows with:

```bat
build_pyinstaller_release.bat
```

Expected package:

```text
dist_release\FH6_Radio_Tool_v3.0.38_nexus_exe_portable.zip
```

---

# FH6 Radio Tool v3.0.38 Build Hotfix

## Fixes

- Fixed `build_pyinstaller_release.bat` failing at `Copying icon to EXE` with `FileNotFoundError: build_pyinstaller\app.ico not found`.
- Root cause: the builder created the temporary icon before the clean step, then deleted `build_pyinstaller`, so PyInstaller could not find the icon later.
- The builder now creates the temporary icon after cleaning. If the icon cannot be generated, it continues without a custom icon instead of failing the whole build.
- Functionality and one-click workflows remain the priority; PyInstaller remains the recommended packaging path.

---

# FH6 Radio Tool v3.0.38 Hotfix

## Fixes

- Fixed the Windows developer/VM error `ImportError: Typelib different than module` from pywinauto/comtypes.
- One-click replacement no longer treats this pywinauto/comtypes import error as a missing environment.
- Added an internal ctypes/Win32 fallback trigger for Fmod Bank Tools Extract/Rebuild. This fallback does not require pywinauto or comtypes.
- Added best-effort cleanup/retry for stale comtypes generated typelib cache.
- PyInstaller packaging is now the recommended build path when Nuitka behaves inconsistently on Windows VMs.

## Recommended build

Run `build_pyinstaller_release.bat` on native Windows. It creates:

`dist_release\FH6_Radio_Tool_v3.0.38_nexus_exe_portable.zip`

Nuitka scripts are still kept, but PyInstaller is the safer packaging option for this release.

---

# FH6 Radio Tool v3.0.38 Hotfix

- Removed the obsolete user-facing **Auto-control Fmod Bank Tools** checkbox from the normal workflow.
- One-click replacement, package generation, and main-menu music replacement now force Fmod Bank Tools GUI automation internally.
- Fixed the legacy path where unchecking the old option could still show a misleading `setup_env.bat` / `pywinauto` prompt in the portable EXE.
- If automation dependencies are missing in a compiled EXE, the tool now reports it as a bad/old build and asks for v3.0.38 or newer, instead of asking users to run old v2 setup scripts.
- Main-menu music replacement remains fixed to `GLB_RadioPressStart.assets.bank`; users only choose the replacement music file.
