# Third-Party Licenses and Integration Notes

FH6 Radio Tool v2.6.2 uses or references the following third-party projects.

## seamless-loop-music

- Project: `CPUrising/seamless-loop-music`
- License: MIT
- Usage: core loop-search ideas were migrated into the internal Python Loop Engine. The original WPF application is not bundled.
- License text: `third_party_licenses/seamless-loop-music/LICENSE`

## PyMusicLooper

- Project: `arkrow/PyMusicLooper`
- License: MIT
- Usage: optional loop-analysis reference/enhancement path. The tool can function without bundling PyMusicLooper.
- License text: `third_party_licenses/PyMusicLooper/LICENSE`

## Fmod Bank Tools

- Project: `Wouldubeinta/Fmod-Bank-Tools`
- License: GPL-3.0
- Usage: external program invocation only. FH6 Radio Tool does not bundle, copy, link, or merge Fmod Bank Tools source or binary files.
- Users select their own installed `Fmod_Bank_Tools.exe` path.

This release package intentionally does not include Fmod Bank Tools binaries.
