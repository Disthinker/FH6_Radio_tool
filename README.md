# FH6 Radio Tool v2.7.3

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

## New in v2.7.3

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
- English guide: `docs/User_Guide_EN.md`
- Third-party license notes: `docs/THIRD_PARTY_LICENSES.md`

## v2.7.3 Notes

- The Marker parameter panel was adjusted so labels and input boxes are paired more clearly.
- The user music list now includes an editable Artist column. The value is saved per track and used when writing XML display information.
- The export button is now named Export markers / 导出 Marker.
