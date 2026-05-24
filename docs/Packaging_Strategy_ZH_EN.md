# FH6 Radio Tool Packaging Strategy / 打包策略

## 当前主线 / Current main path

从 v2.7.16 开始，`build_portable_release.bat` 不再只是压缩源码目录，而是构建 **Windows 免安装 EXE 目录版**：

- 输出：`dist_release/FH6_Radio_Tool_vX.Y.Z_exe_portable.zip`
- 用户解压后直接运行：`FH6RadioTool.exe`
- 不需要用户先安装 Python 或运行 `setup_env.bat`
- Fmod Bank Tools 仍然不内置，用户需要在工具设置中选择自己的 `Fmod_Bank_Tools.exe`

## 为什么使用 PyInstaller one-folder / Why PyInstaller one-folder

之前 Nuitka standalone 在不同虚拟机环境中出现过资源写入、pywinauto/comtypes、Fmod Bank Tools 自动化时序等不稳定问题。当前主线改为 PyInstaller one-folder：

- 构建脚本更短、更容易复现
- PySide6 / Qt 插件由 PyInstaller hooks 处理
- imageio-ffmpeg 的 ffmpeg 会额外复制到 `tools/ffmpeg.exe`
- `output/backup/work` 固定写在 `FH6RadioTool.exe` 同目录，方便普通玩家理解和恢复

## 构建方式 / Build

在 Windows 中运行：

```bat
build_portable_release.bat
```

构建成功后测试：

```text
dist_release/FH6_Radio_Tool_vX.Y.Z_exe_portable.zip
```

解压该 zip，双击 `FH6RadioTool.exe`。

## 源码批处理备用包 / Source fallback

如果 EXE 版遇到不可复现的问题，可以使用源码批处理方式：

```bat
setup_env.bat
run_tool.bat
```

开发者也可以运行：

```bat
build_source_release.bat
```

生成源码批处理备用包。


## v2.7.17 PyInstaller 精简收集策略 / Lean PyInstaller collection

v2.7.17 修正了 EXE 构建脚本：不再使用 `--collect-all PySide6`。FH6 Radio Tool 只使用 QtCore、QtGui、QtWidgets、QtMultimedia 等模块，不使用 QML、Quick、Charts 或 WebEngine。过度收集整个 PySide6 会导致 PyInstaller 分析 QtQml/QtQuick 插件，出现大量无关日志，甚至触发缺失 QML 插件的 logging error。

The EXE builder now lets PyInstaller collect only the PySide6 modules actually used by the app and explicitly excludes QML/Quick/Charts/WebEngine families. This makes packaging faster and avoids unrelated QtQml hook warnings.
