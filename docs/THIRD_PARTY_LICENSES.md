# Third-Party Licenses and Integration Notes

FH6 Radio Tool uses or references the following third-party projects.

## seamless-loop-music

- Project: `CPUrising/seamless-loop-music`
- License: MIT
- Usage: the native `loopfinder` analysis engine is vendored under `third_party/loopfinder` and can be built into `loopfinder.dll` for high-quality Loop candidate analysis. The original WPF application is not bundled.
- License text: `third_party_licenses/seamless-loop-music/LICENSE`

## aubio

- Project: `aubio/aubio`
- License: GPL-3.0-or-later
- Usage: vendored by the upstream `loopfinder` native engine for beat detection.
- License text: `third_party_licenses/aubio/COPYING`
- Source availability: the complete source used for builds is included under `third_party/loopfinder/third_party/aubio`.

## PyMusicLooper

- Project: `arkrow/PyMusicLooper`
- License: MIT
- Usage: optional fallback loop-analysis path. The tool can function without bundling PyMusicLooper.
- License text: `third_party_licenses/PyMusicLooper/LICENSE`

## Fmod Bank Tools

- Project: `Wouldubeinta/Fmod-Bank-Tools`
- License: GPL-3.0
- Usage: external program invocation only. FH6 Radio Tool does not bundle, copy, link, or merge Fmod Bank Tools source or binary files.
- Users select their own installed `Fmod_Bank_Tools.exe` path.

This release package intentionally does not include Fmod Bank Tools binaries.
