# FH6 Radio Tool - Nuitka Packaging Notes

## 中文

本项目新增 Nuitka 打包入口：

- `build_nuitka_release.bat`
- `build_nuitka_entry.py`
- `scripts/build_nuitka_release.py`

推荐在原生 Windows 中执行：

```bat
build_nuitka_release.bat
```

输出文件位于：

```text
dist_release\FH6_Radio_Tool_v*_nexus_nuitka_onefile.zip
```

该 ZIP 的目标是适合 Nexus Mods 上传：

- 不包含 loose `.ico` 文件；图标在构建时嵌入 EXE。
- 不包含源码目录 `fh6_radio_tool/`。
- 不包含嵌套 `.zip`、`.7z` 等压缩包。
- 不包含游戏文件、音乐文件或原始 bank 文件。
- Fmod Bank Tools 不会被内置，用户仍需自行选择自己的 `Fmod_Bank_Tools.exe`。

注意：不要在 WSL/Linux 中构建 Windows EXE。Nuitka 对 PySide6/Qt 的 Windows 打包需要原生 Windows Python 环境和 Windows 编译器。

## English

New Nuitka packaging entry points:

- `build_nuitka_release.bat`
- `build_nuitka_entry.py`
- `scripts/build_nuitka_release.py`

Run on native Windows:

```bat
build_nuitka_release.bat
```

Output:

```text
dist_release\FH6_Radio_Tool_v*_nexus_nuitka_onefile.zip
```

The ZIP is intended to be Nexus Mods friendly:

- No loose `.ico`; the icon is embedded into the EXE.
- No source folder `fh6_radio_tool/`.
- No nested archive files such as `.zip` or `.7z`.
- No game files, music files, or original bank files.
- Fmod Bank Tools is not bundled; users select their own `Fmod_Bank_Tools.exe`.

Do not build the Windows EXE from WSL/Linux. Use native Windows Python and a supported Windows C/C++ compiler.
