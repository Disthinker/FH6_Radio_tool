# FH6 Radio Tool v2.6.2 User Guide

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

Before overwriting game files, the tool creates a backup manifest. You can restore original files through **Restore Default Files**.

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
