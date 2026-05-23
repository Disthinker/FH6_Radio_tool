# FH6 Radio Tool v2.7.4 User Guide

## 1. Requirements

Prepare the following items first:

- Python 3.10 or newer.
- Your FH6 game root folder.
- Your replacement music files. WAV is recommended.
- Fmod Bank Tools. FH6 Radio Tool does not bundle it; you need to download it separately and select `Fmod_Bank_Tools.exe` inside the tool.

Recommended simple paths:

```text
E:\FH6RadioTool
E:\Music
E:\FmodBankTool
```

Avoid very long paths, unusual symbols, and restricted system folders.

## 2. Install Dependencies

For first-time setup, run:

```text
setup_env.bat
```

This creates a local `.venv` environment and installs dependencies there. Your global Python installation will not be modified.

After setup, start the tool with:

```text
run_tool.bat
```

## 3. Setup

The setup panel is shown by default on first launch. Select:

1. Game root folder.
2. Music folder.
3. UI language.
4. Game language / Game XML.

After selecting the game root, the tool scans the game folder and selects the matching `RadioInfo*.xml` file. Changing the game language will also make the tool try to switch to the corresponding XML file.

After setup is complete, the setup panel can be hidden. You can show it again through the side setup tab.

## 4. Step 1: Choose Radio and Tracks

In **Step 1 · Choose Radio and Tracks**:

1. Select a target radio station.
2. Tick the original game track slots you want to replace on the left.
3. Tick your own music files on the right.
4. The number of selected slots and music files must match.
5. Click **Apply Selected Replacement**.

For batch replacement, select multiple slots and the same number of music files. Pairing is based on list order.

## 5. Step 2: Set Loop Points

In **Step 2 · Set Loop Points**:

1. Select an audio file.
2. Click **Analyze Loop** to generate loop candidates.
3. Choose a candidate from the dropdown list.
4. Click **Preview Candidate** to check the loop transition.
5. Use scene preview to simulate free roam, race loop, finish, and post-race loop behavior.
6. Use the progress bar and Marker controls for manual adjustment.
7. Save the current audio settings.

If the automatic candidate is not good enough, manually move the progress bar and write the current position into the target Marker.


## Marker Reference

The names below are based on the current FH radio XML workflow and community testing. Some markers may behave slightly differently depending on the radio station and bank, so always preview in the tool and test in game.

### Common playback markers

- **TrackStart**: The normal start position of the song. Usually `0`.
- **TrackLoopStart**: The start of the main loop used during normal/race playback.
- **TrackLoopEnd**: The end of the main loop. When the loop is active, playback should jump back to `TrackLoopStart`.
- **PostRaceLoopStart**: The start of the post-race loop section.
- **PostRaceLoopEnd**: The end of the post-race loop section. When the post-race loop is active, playback should jump back to `PostRaceLoopStart`.
- **End**: The end sample of the audio file. Usually this should be the last valid sample.

### Less obvious markers

- **TrackDrop**: A high-energy entry point for the track, usually used around race start or when the game wants to jump into a stronger part of the song. A good placement is the first chorus/drop/main beat after the intro. If the song has no clear drop, use `TrackLoopStart` or a musically strong point.

- **PostDrop**: A transition point used for finish/post-race style playback. A good placement is a chorus, final drop, or another energetic section that sounds natural after the race finish. If unsure, place it near `PostRaceLoopStart` or reuse a strong chorus/drop point.

- **DJSegment**: A marker related to DJ/radio segment insertion. For normal custom song replacement, this is usually not needed. If you do not have a specific DJ segment, leave it as `-1`.

- **DJStart**: The start point for a DJ/radio voice segment. For normal music replacement, leave it as `-1` unless you intentionally prepare a DJ/voice section.

- **StingerStart**: A marker for a short stinger/transition sound. Most custom songs do not need this. Leave it as `-1` unless you know the track has a dedicated stinger section.

### Practical recommendations

For most custom songs, start with this simple strategy:

1. Set `TrackStart` to `0`.
2. Set `End` to the final sample of the file.
3. Choose a stable chorus or main beat loop for `TrackLoopStart` and `TrackLoopEnd`.
4. Set `TrackDrop` to the first strong drop/chorus after the intro.
5. Set `PostDrop` to a strong chorus/final drop, or near `PostRaceLoopStart`.
6. Set `PostRaceLoopStart` and `PostRaceLoopEnd` to a section that can loop after a race.
7. Leave `DJSegment`, `DJStart`, and `StingerStart` as `-1` unless you specifically need them.

Automatic loop candidates are only a helper. Manual preview and fine tuning are still strongly recommended.


## 6. Step 3: Generate or Replace

In **Step 3 · Generate or Replace**, select your Fmod Bank Tools executable first.

Then choose one final action:

### Generate Mod Output Package

This does not overwrite game files. The patched XML and rebuilt bank files will be placed in the `output` folder. Use this for checking or manual installation.

### One-Click Replace Game Files

This workflow automatically runs:

```text
Prepare Fmod Bank Tools workspace
→ Extract target bank
→ Generate patched WAV/XML files
→ Rebuild bank
→ Back up original XML/bank files
→ Replace game files
```

Before overwriting game files, the tool creates two kinds of backups:

1. Initial state backup: captured the first time the tool touches a game XML/bank file.
2. Backup point: captured before each one-click replacement, so you can restore to the state before that specific modification.

Use **Restore backup / initial state** if you need to roll back.

## 7. About Fmod Bank Tools

FH6 Radio Tool does not include Fmod Bank Tools. Select its `Fmod_Bank_Tools.exe` path in the final step.

During Extract/Rebuild, do not close the Fmod Bank Tools window manually. If GUI automation does not work, make sure you have run `setup_env.bat` and that `pywinauto` is installed correctly.

## 8. Troubleshooting

### No bank files found

Check that the game root folder is correct and that the selected Fmod Bank Tools exe path is valid.

### CU1 bank has no FSB audio

Some `R*_Tracks_CU1.assets.bank` files may not contain extractable FSB audio for Fmod Bank Tools. The tool will try to switch to an extractable bank from the same radio station and report it in the runtime log.

### One-click replacement failed

Check the runtime log on the right. Do not manually copy incomplete output into the game folder. Try **Generate Mod Output Package** first for safer testing.

### XML loading failed

Use the original XML from the game files. Avoid using XML files that were already damaged by manual editing or other tools.

## 9. Backup and Restore

Use **Back Up Current Game Files** before large changes.

Use **Restore Default Files** to restore original XML and bank files from the backup manifest.

## Batch Marker Import

Starting from v2.7.2, FH6 Radio Tool supports batch importing Marker parameters from CSV or JSON files.

The recommended format is CSV. A blank template is included here:

- `docs/examples/marker_import_template.csv`

An import-ready example converted from the provided song marker spreadsheet is also included:

- `docs/examples/marker_import_from_uploaded_song_samples.csv`

How to use:

1. Select and scan your music folder first.
2. Go to the loop marker step.
3. Click **Import markers** in the Marker parameter area.
4. Select a CSV or JSON import file.
5. The tool will first match rows by filename / display name. If name matching fails, it will try unique `SampleLength` matching.

Recommended CSV columns:

`MatchName, Filename, DisplayName, Artist, SampleRate, SampleLength, TrackStart, TrackDrop, TrackLoopStart, TrackLoopEnd, PostDrop, PostRaceLoopStart, PostRaceLoopEnd, DJSegment, StingerStart, DJStart, End`

Note: After importing, manual preview is still recommended, especially for TrackLoop and PostRaceLoop transitions.

## v2.7.3 Notes

- In the station/music step, the user music list now has an editable Artist column.
- The edited Artist value is saved per track and used for in-game XML display.
- The previous "Export marker template" button is now named "Export markers".
