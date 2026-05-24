
## v3.0.27 正式版整理 / Release cleanup

- 移除临时开发测试按钮，普通用户界面更简洁。
- 优化 Marker 参数区按钮布局，避免按钮堆叠拥挤。
- 保留已验证的多 Track bank 替换、跨语言 RadioInfo 同步、安全 Marker、备份恢复状态清理等正式功能。


## v2.7.22 - Dev all-station mapping test / preview audio fix

- Added a temporary developer-only all-radio station matching test button. It runs Extract per radio station and writes `work/dev_all_station_match_test/dev_all_station_match_summary.csv`.
- Fixed bounded preview audio quality: fade-in is now applied only at the real preview start instead of every streaming chunk.
- Relaxed loop preview validation so LoopStart may be earlier than the preview window, which is required to preview loop jumps correctly.
- Added XML-only/no-FMOD-audio skip handling for trailing metadata-only radio rows so they do not block valid playable slots or generate misleading renamed-but-not-replaced entries.

# FH6 Radio Tool v2.7.22

This is a hotfix release for the v2.7.9 startup crash.

这是用于修复 v2.7.9 启动崩溃的热修复版本。


## 免安装 EXE 打包 / EXE Portable Build

开发者在 Windows 中运行：

```bat
build_portable_release.bat
```

该脚本会生成真正的免安装 EXE 目录版：

```text
dist_release/FH6_Radio_Tool_v2.7.22_exe_portable.zip
```

玩家解压后双击 `FH6RadioTool.exe` 即可运行。Fmod Bank Tools 仍然需要用户自行选择外部 exe，工具不会内置或分发它。


## New in v2.7.16

- Rolled the code base back to the stable v2.7.11 line before applying focused UI/Loop fixes.
- Fixed the target radio combo box width after the first game-root scan.
- Changed Loop analysis default action to batch-analyze all scanned songs.
- Added **Save all audio settings / 保存全部音频设置** so users no longer need to save one song at a time.
- Updated safe default markers for normal songs:
  - `TrackDrop = 0`
  - `PostDrop = 0`
  - `TrackLoopStart = 0`
  - `PostRaceLoopStart = 0`
  - `TrackLoopEnd` and `PostRaceLoopEnd` remain unset until a candidate/import is applied.
- `build_portable_release.bat` now builds a real PyInstaller one-folder EXE portable package instead of only zipping the source tree.

## v2.7.16 中文说明

- 以稳定的 v2.7.11 为基线回退后，只合入明确的 UI / Loop 功能修复。
- 修复第一次设置游戏根目录后“目标电台”下拉框宽度异常的问题。
- Loop 分析默认改为批量分析当前音乐目录中的全部歌曲。
- 新增 **保存全部音频设置**，避免每次只能保存一首歌。
- 调整普通歌曲安全默认 Marker：
  - `TrackDrop = 0`
  - `PostDrop = 0`
  - `TrackLoopStart = 0`
  - `PostRaceLoopStart = 0`
  - `TrackLoopEnd` / `PostRaceLoopEnd` 默认保持未设置，等用户应用候选或导入 Marker 后再写入。
- 新增 `build_portable_release.bat` 作为当前正式稳定打包脚本；Nuitka standalone 暂缓为实验方向。

### FFmpeg note

Starting from v2.7.6, `setup_env.bat` installs `imageio-ffmpeg`, which provides a bundled FFmpeg binary for audio conversion. In most cases, users do not need to manually add `ffmpeg.exe` to the system PATH. If the tool still reports FFmpeg missing, run `setup_env.bat` again.

### FFmpeg 说明

从 v2.7.6 开始，`setup_env.bat` 会安装 `imageio-ffmpeg`，用于自动提供音频转换所需的 FFmpeg。通常不再需要用户手动把 `ffmpeg.exe` 加入系统 PATH。如果仍提示找不到 FFmpeg，请重新运行 `setup_env.bat`。


## Installation / 安装启动

This package uses batch scripts for setup and launch:

1. Run `setup_env.bat` once to create `.venv` and install dependencies.
2. Run `run_tool.bat` to start FH6 Radio Tool.
3. Run `cleanup_env.bat` only if you want to remove the local environment.

本发布包使用批处理脚本安装与启动：

1. 首次运行 `setup_env.bat` 创建 `.venv` 并安装依赖。
2. 之后运行 `run_tool.bat` 启动工具。
3. 如需清理本地运行环境，可运行 `cleanup_env.bat`。


FH6 Radio Tool is a community tool for simplifying custom radio music replacement in Forza Horizon 6. The tool helps users scan game files, select radio tracks, replace selected songs, adjust loop markers, generate a mod output package, and optionally run a safer one-click replacement workflow with backup and restore support.

## Quick Start

1. Install **Python 3.10 or newer**.
2. Extract this package to a simple path, for example `E:\FH6RadioTool`.
3. Run `setup_env.bat` once.
4. Run `run_tool.bat`.
5. Select your FH6 game root folder and your music folder.
6. Select a radio station, tick the original slots to replace, tick the same number of custom music files, then click **Apply Selected Replacement**.
7. Set or import loop marker parameters if needed.
8. Choose one final action:
   - **Generate Mod Output Package**: generate patched XML and rebuilt bank files in the `output` folder without overwriting the game.
   - **One-Click Replace Game Files**: automatically back up the original XML/bank files, then replace them.

## New in v2.7.8

- Added two-level backup strategy: initial-state backup plus point-in-time backup points.
- Added restore options for either initial state or a selected backup manifest.

- Added Marker reference documentation explaining TrackDrop, PostDrop, DJSegment, StingerStart, and DJStart.
- Added separate English and Chinese Marker reference pages in `docs/`.
- Fixed English UI text clipping by relaxing overly fixed button sizes.
- Fixed the finish scene preview issue where playback could jump to the end immediately.
- Reorganized Marker parameters into clearer rows:
  - TrackStart / TrackDrop / PostDrop
  - TrackLoopStart / TrackLoopEnd
  - PostRaceLoopStart / PostRaceLoopEnd
  - DJSegment / StingerStart / DJStart
  - End
- Added batch Marker import from CSV or JSON.
- Added Marker import template export.
- Added an example import CSV converted from the provided song marker spreadsheet.

## Marker Import Format

Marker import currently supports CSV and JSON. The recommended format is CSV.

Example files are included in:

- `docs/examples/marker_import_template.csv`
- `docs/examples/marker_import_from_uploaded_song_samples.csv`

Important columns:

- `MatchName`, `Filename`, or `DisplayName`: used to match rows to music files in your selected music folder.
- `SampleRate`, `SampleLength`: optional metadata and fallback matching information.
- Marker columns: `TrackStart`, `TrackDrop`, `TrackLoopStart`, `TrackLoopEnd`, `PostDrop`, `PostRaceLoopStart`, `PostRaceLoopEnd`, `DJSegment`, `StingerStart`, `DJStart`, `End`.

The importer first matches by filename/display name, then falls back to unique `SampleLength` matching when possible.

## Required External Tool

This package does **not** include Fmod Bank Tools. To use Extract/Rebuild automation, download Fmod Bank Tools separately and select its `Fmod_Bank_Tools.exe` path in FH6 Radio Tool.

Fmod Bank Tools is treated as an external program. FH6 Radio Tool only prepares its working folders/config and attempts to automate its GUI.

## Important Notes

- Always keep a backup of your original game files.
- Automatic loop candidate detection is experimental. Manual listening and marker adjustment are still recommended.
- Some `CU1` banks may contain no extractable FSB audio; the tool may automatically switch to a same-station extractable bank.
- Use short, simple paths when possible, such as `E:\FH6RadioTool`, `E:\Music`, and `E:\FmodBankTool`.

## Documentation

- Chinese guide: `docs/User_Guide_ZH.md`
- Chinese marker reference: `docs/Marker_Reference_ZH.md`
- English guide: `docs/User_Guide_EN.md`
- English marker reference: `docs/Marker_Reference_EN.md`
- Third-party license notes: `docs/THIRD_PARTY_LICENSES.md`

## v2.7.x Notes

- The Marker parameter panel was adjusted so labels and input boxes are paired more clearly.
- The user music list now includes an editable Artist column. The value is saved per track and used when writing XML display information.
- The export button is now named Export markers / 导出 Marker.


## Marker Documentation Update

- Added explanations for less obvious marker fields such as `TrackDrop`, `PostDrop`, `DJSegment`, `StingerStart`, and `DJStart`.
- Added practical marker placement recommendations for normal custom song replacement.

## Backup strategy

Starting from v2.7.8, the tool uses a two-level backup strategy:

- **Initial state backup**: the first time the tool touches a game XML or bank file, it stores a baseline copy. This lets users restore the files back to the earliest state captured by the tool. To save disk space, only touched XML/bank files are copied, not the whole game folder.
- **Backup points**: before each one-click replacement or manual backup, the tool also creates a point-in-time backup. Users can restore a specific backup point if they want to go back to the state before a certain modification.

If the game was already modified before the first backup, the initial state captured by the tool will be that already-modified state. For the best result, create a backup before the first replacement.

## v2.7.15 Packaging Fix

- Fixed the portable release builder error caused by literal `^` characters being passed to PowerShell.
- 打包脚本已修复：不再把批处理转义符 `^` 传给 PowerShell，避免清理缓存和 #Uxxxx 检查误报。

### v2.7.21 user-feedback fixes

This build adds safer XML metadata validation, bank/extract diagnostics, No Loop / Safe Marker buttons, final WAV SampleLength checks, and safer loop preview range handling. If a selected slot cannot be safely mapped to an FMOD extracted WAV, generation stops before XML/Rebuild/overwrite.



## v3.0.12 Restore State Fix

- Restoring a backup or initial state now also clears pending in-tool replacement assignments.
- Old `current_assignment_mapping.csv` / replacement plan caches are removed after restore to prevent stale changes from being applied again on the next one-click replacement.



## v3.0.16

- Rolled back the unverified R1 -> GLB_Radio_3D cross-bank replacement mapping. User listening tests showed those candidates were incorrect.
- R1/Horizon Pulse slot 30/32 are now hidden from normal replacement again until their real bank/sound locations are confirmed.
- Cleared the default `config/known_cross_bank_music_map.csv` so no wrong GLB_Radio_3D mapping is applied automatically.
- Developer all-bank scan remains available for locating the real audio source before cross-bank replacement is re-enabled.


## v3.0.19

- Multi-track-bank station model: each radio station can use multiple `R*_Tracks_*` banks, such as `R1_Tracks_CU1` plus `R1_Tracks_Disk`.
- Fixes R1/R2 slot limits caused by earlier CU1-only scanning.
- Old diagnostic slot-profile files no longer hide R1/R2 Disk-bank songs.


## v3.0.19 - Station Track Bank Search Order

- Improved radio-bank matching: the tool now searches the current station main Track bank first, then other same-station `R*_Tracks_*` banks such as `Disk` or `CU2`, and only then treats `GLB_Radio_3D` as a low-confidence candidate source when available.
- Same-station `R*_Tracks_Disk` / `R*_Tracks_CU2` banks are now collected automatically from FMODBanks even if the RadioInfo XML bank list is incomplete.
- This is intended to support stations whose playable songs are split across multiple bank files, while avoiding accidental matches from unrelated stations.


## v3.0.38

- Added the official application icon to the PyInstaller EXE build.
- The main window also uses the bundled icon when running from source or from the portable EXE package.

## v3.0.34 main menu / press-start music replacement

The main menu music target is now fixed to `GLB_RadioPressStart.assets.bank`.
Users do not need to choose the bank manually. After selecting the FH6 game root, choose only the replacement audio file in the optional main-menu music panel. The tool will automatically find the bank under `media/Audio/FMODBanks`, replace its single music entry, and then either generate a mod package or perform one-click replacement with backup.

主菜单 / Press Start 音乐目标已固定为 `GLB_RadioPressStart.assets.bank`。用户不再需要手动选择 bank。选择 FH6 游戏根目录后，只需要在主菜单音乐区域选择想替换进去的音乐文件，工具会自动从 `media/Audio/FMODBanks` 定位该 bank，替换其中唯一音乐音频，然后生成 Mod 包或一键替换并自动备份。



## v3.0.34 hotfix

- Fixed the misleading v3.0.33 automation prompt that asked portable EXE users to run `setup_env.bat` even though the Nexus/Nuitka package does not include it.
- The Nuitka builder now treats `pywinauto`/Win32 automation dependencies as required and includes the relevant packages more aggressively.
- The Nexus Nuitka package no longer bundles a separate `tools/ffmpeg.exe` by default, reducing quarantine risk.

## v3.0.38 hotfix

- Removed the obsolete user-facing **Auto-control Fmod Bank Tools** checkbox from the normal workflow.
- One-click replacement, package generation, and main-menu music replacement now force Fmod Bank Tools automation internally.
- Fixed the legacy path where unchecking the old option could still show a misleading `setup_env.bat` / `pywinauto` prompt in the portable EXE.
- Portable EXE users should not use old v2 `setup_env.bat` files to repair the compiled program; update to v3.0.38 or newer instead.


## v3.0.38 build hotfix

- Fixed PyInstaller BAT build failure where `build_pyinstaller\app.ico` was deleted by the clean step before PyInstaller copied it into the EXE.
- The builder now regenerates the temporary icon after cleaning, and falls back to a no-custom-icon build if icon generation fails.
- Use `build_pyinstaller_release.bat` from this package; do not reuse older v3.0.30/v3.0.36 build folders.

