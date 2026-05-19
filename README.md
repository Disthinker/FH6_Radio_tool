# FH6 Radio Tool v2.6.2

FH6 Radio Tool is a community tool for simplifying custom radio music replacement in Forza Horizon 6. This release focuses on a guided workflow: choose the game folder, choose your music folder, select the radio tracks to replace, set or preview loop points, then generate a mod output package or run the safer one-click replacement workflow.

## Quick Start

1. Install **Python 3.10 or newer**.
2. Download and extract this package to a simple path, for example `E:\FH6RadioTool`.
3. Run `setup_env.bat` once.
4. Run `run_tool.bat`.
5. In the tool, select your FH6 game root folder and your music folder.
6. Select a radio station, tick the original track slots you want to replace, tick the same number of your own music files, then click **Apply Selected Replacement**.
7. Set loop points if needed.
8. Choose one final action:
   - **Generate Mod Output Package**: generate patched XML and rebuilt bank files in the `output` folder without overwriting the game.
   - **One-Click Replace Game Files**: automatically back up the original XML/bank files, then replace them.

## What This Version Includes

- Guided workflow UI with Back/Next navigation.
- Chinese/English interface switching.
- Game language/XML selection for different `RadioInfo*.xml` files.
- Radio station and track listing from game XML.
- Batch selection and batch replacement.
- Per-track setting storage.
- Internal loop candidate analysis and preview.
- Track/Post loop marker filling and manual marker adjustment.
- Fmod Bank Tools external automation.
- Automatic original file backup before game-file replacement.
- Restore default files from backup manifest.

## Required External Tool

This package does **not** include Fmod Bank Tools. To use Extract/Rebuild automation, download Fmod Bank Tools separately and select its `Fmod_Bank_Tools.exe` path in FH6 Radio Tool.

Fmod Bank Tools is treated as an external program. FH6 Radio Tool only writes its working folders/config and attempts to automate its GUI. This avoids bundling GPL-licensed third-party binaries in this package.

## Important Safety Notes

- Always keep a backup of your original game files.
- The one-click replacement workflow automatically creates a backup before overwriting files, but manual backups are still recommended.
- If the rebuilt bank fails, do not force-copy partial output into the game directory.
- If Fmod Bank Tools selects a different extractable bank than expected, check the runtime log. Some `CU1` banks may contain no extractable FSB audio; the tool may automatically switch to the same-station extractable bank.
- Use short, simple paths when possible, such as `E:\FH6RadioTool`, `E:\Music`, and `E:\FmodBankTool`.

## Documentation

- Chinese guide: `docs/User_Guide_ZH.md`
- English guide: `docs/User_Guide_EN.md`
- Third-party license notes: `docs/THIRD_PARTY_LICENSES.md`

## Files You Usually Need

- `setup_env.bat` — install Python dependencies into `.venv`.
- `run_tool.bat` — start FH6 Radio Tool.
- `cleanup_env.bat` — remove the local virtual environment if you want to reinstall dependencies.
- `requirements.txt` — dependency list.
- `fh6_radio_tool/` — source code.
- `third_party_licenses/` — required license texts for third-party MIT components.

## Current Release

Version: **v2.6.2 formal release package**

This release package has removed old preview readme files, old audit logs, and development-only documents from previous iterations.



## Acknowledgements / 致谢

FH6 Radio Tool v2 was not created in isolation. During the development of v2, we referred to several community tools, open-source projects, and user-made tutorials that helped clarify the full radio replacement workflow.

Special thanks to the author of this Bilibili tutorial:

https://www.bilibili.com/opus/1203552915611975682#reply299863540801

The tutorial provided a very clear explanation of the overall process, especially the workflow around extracting banks, replacing tracks, rebuilding files, and thinking about loop point selection. It helped us better understand which parts of the old workflow were too complicated for normal users, and directly inspired the goal of making FH6 Radio Tool v2 more integrated and beginner-friendly.

We would also like to thank the following projects and tools:

\- **Fmod Bank Tools** — used as the external FMOD bank extract / rebuild tool.
\- **seamless-loop-music** — provided useful reference ideas for loop detection, loop preview, and seamless loop workflows.
\- **PyMusicLooper** — provided reference ideas for automatic music loop point detection.

Please note that FH6 Radio Tool v2 uses Fmod Bank Tools as an external tool and does not bundle it directly. The loop candidate feature in the current version is still experimental and usually requires manual adjustment.

Thanks to all tutorial authors, tool developers, and community users who shared feedback, reported issues, and helped improve the FH6 radio replacement workflow.
