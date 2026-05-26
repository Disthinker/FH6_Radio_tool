# FH6 Radio Tool

FH6 Radio Tool 是一个用于《Forza Horizon 6》电台音乐替换的 Windows 桌面工具。它基于 Python / PySide6 开发，重点是让普通玩家可以用自己的音乐替换游戏电台歌曲，并尽量减少手工编辑 XML、手动转换音频和手动管理备份的成本。

当前版本：`v3.1.4-dev`

## 功能概览

- 扫描游戏目录，自动定位 `RadioInfo_*.xml` 和 `media/Audio/FMODBanks`。
- 选择电台和原曲槽位，将自定义音乐分配到指定 slot。
- 自动准备替换音频，统一输出为适合 FMOD rebuild 的 WAV。
- 编辑、试听、导入、导出 TrackDrop / PostDrop / Loop marker。
- 生成 patched XML 和 `fmod_ready_wav` rebuild 工作区。
- 自动调用 Fmod Bank Tools 执行 Extract / Rebuild。
- 生成 Mod 输出包，或一键备份并替换游戏文件。
- 支持主菜单 / Press Start 音乐替换：`GLB_RadioPressStart.assets.bank`。
- 提供开发者模式，用于全 bank Extract、统计和 XML-bank 映射研究。

## v3.1.4-dev 更新重点

相比 `v3.1.3`，`v3.1.4-dev` 主要修复用户反馈的稳定性和 marker/audio 问题：

- Marker Export / Import 现在会导出当前已分配音乐的完整配置，可作为批量编辑模板。
- Export 会同步当前 UI 中尚未保存的 marker 数值。
- 播放器改为 Play / Pause / Resume / Reset 语义，暂停不再清零。
- 进度条和波形支持点击、拖动 seek。
- 波形上显示 TrackDrop / PostDrop 竖线，以及 TrackLoop / PostRaceLoop 区间。
- XML 写入前会读取最终 `fmod_ready_wav` 的真实 sample length。
- 如果转换前后 sample 数变化，marker 会按比例缩放到最终 WAV 坐标系。
- 应用替换时会先生成 marker 编辑用 prepared WAV，之后试听、波形和 marker 编辑都基于处理后的音频。
- `SampleLength`、`End` 和已存在的 duration-like 字段会使用最终 WAV 的真实值。
- 同一 `SoundName` 的重复 XML 节点会同步更新，减少改错节点导致的游戏内偏移。
- 音频导入统一标准化为 PCM WAV、48000 Hz、Stereo、16-bit。
- 增加响度匹配、峰值保护和 Runtime Log 音频处理报告。
- 修复 Fmod Extract 模板或外部工具窗口文本为空时可能出现的 `NoneType.strip` 崩溃。

## 环境要求

- Windows 10 / 11。
- Python 3.10 或更新版本，用于源码运行。
- Fmod Bank Tools，需要用户自行下载并在工具中选择 `Fmod_Bank_Tools.exe`。
- 足够的磁盘空间用于 Extract / Rebuild 输出和备份。

依赖见 [requirements.txt](requirements.txt)：

- `PySide6`
- `pywinauto`
- `imageio-ffmpeg`

`setup_env.bat` 会创建 `.venv` 并安装依赖。`imageio-ffmpeg` 通常会提供可用的 FFmpeg，无需用户手动配置 PATH。

## 快速开始

1. 将项目解压或 clone 到简单路径，例如 `E:\FH6RadioTool`。
2. 首次运行：

   ```bat
   setup_env.bat
   ```

3. 启动工具：

   ```bat
   run_tool.bat
   ```

4. 在工具中选择游戏根目录和音乐目录。
5. 选择目标电台，勾选要替换的原曲 slot。
6. 勾选相同数量的自定义音乐，点击应用替换。
7. 试听并调整 marker，必要时导入或导出 marker CSV。
8. 选择最终操作：
   - 生成 Mod 包：只生成输出文件，不覆盖游戏。
   - 一键替换到游戏：自动备份后覆盖 XML 和 bank。

## 常用工作流程

### 普通电台音乐替换

1. 扫描游戏目录。
2. 扫描音乐目录。
3. 选择电台和 slot。
4. 应用替换。
5. 编辑 marker。
6. 生成 Mod 包或一键替换。

工具会在流程中准备音频、生成 XML、创建 `fmod_ready_wav`，并调用 Fmod Bank Tools 进行 Extract / Rebuild。

### Marker 批量编辑

1. 在工具中分配歌曲。
2. 点击 Export markers / 导出 Marker。
3. 使用 Excel、WPS 或 LibreOffice 编辑 CSV。
4. 点击 Import markers / 导入 Marker。
5. 检查 UI 中 marker 是否刷新。
6. 再次导出或生成包。

CSV 默认使用 UTF-8-SIG 编码，marker 单位为 samples。

### 主菜单音乐替换

主菜单 / Press Start 音乐目标固定为：

```text
GLB_RadioPressStart.assets.bank
```

用户只需要选择新的主菜单音乐文件。工具会根据游戏根目录自动定位目标 bank，并执行生成包或一键替换流程。

### 开发者模式

开发者模式用于研究音频 bank 结构，不建议普通玩家日常使用。它可以：

- Extract 全部候选 bank。
- 生成 bank 音频统计。
- 生成 XML 到 bank / sound 的映射 CSV。
- 分析 DJ、stinger、音效和跨 bank 关系。

## 音频处理说明

导入音频会被统一处理为：

```text
PCM WAV
48000 Hz
Stereo
16-bit
```

工具会分析源音频响度和峰值，应用安全增益，并在最终替换到 `fmod_ready_wav` 时对照原始 FMOD 提取音频进行轻量响度匹配。处理结果会写入 Runtime Log 和 `work/volume_match_report.csv`。

支持 FFmpeg 可读取的常见格式，例如 WAV、FLAC、MP3、M4A、AAC、OGG、WMA。

## Marker 与 XML 写入说明

工具内部 marker 使用 sample 坐标。普通替换流程中，用户点击应用替换后会先生成 prepared WAV；之后的试听、波形和 marker 编辑都基于该 prepared WAV。生成 XML 前，工具仍会以最终 ready WAV 为准复核：

- `TrackDrop`
- `PostDrop`
- `TrackLoopStart`
- `TrackLoopEnd`
- `PostRaceLoopStart`
- `PostRaceLoopEnd`
- `End`

如果源音频和最终 WAV 的 sample length 不一致，工具会按比例缩放 marker，避免游戏内 loop 起点、终点和工具内试听位置明显不一致。

## 输出目录

常见生成目录：

- `output/`：最终输出 XML、ready WAV、rebuild bank、manifest。
- `work/`：中间文件、诊断报告、映射 CSV、运行数据库。
- `backup/`：一键替换和手动备份产生的恢复点。
- `dist_release/`：打包脚本生成的发布包。

这些目录属于本地生成内容，默认不会提交到 Git。

## 构建

推荐优先使用 PyInstaller 打包：

```bat
build_pyinstaller_release.bat
```

Nuitka 构建脚本仍保留，用于实验或备用：

```bat
build_nuitka_release.bat
```

源码包构建：

```bat
build_source_release.bat
```

构建产物默认输出到 `dist_release/`，不会提交到仓库。

## 故障排查

### 找不到 FFmpeg

先重新运行：

```bat
setup_env.bat
```

如果仍失败，可以在系统 PATH 中安装 FFmpeg，或在工具设置中选择自己的 `ffmpeg.exe`。

### Fmod Bank Tools 没有自动点击 Extract / Rebuild

- 确认选择的是 `Fmod_Bank_Tools.exe`。
- 不要在 Extract / Rebuild 过程中关闭 Fmod Bank Tools。
- 如果自动点击失败，请查看 Runtime Log 中的 Win32 / pywinauto 提示。

### 生成包失败但游戏文件未被覆盖

这是预期保护行为。工具会先完成 Extract / XML / Rebuild 校验，再执行覆盖。若中途失败，请查看 Runtime Log 和 `work/` 中的诊断报告。

### 游戏内播放位置与工具试听不一致

请使用 `v3.1.4-dev` 或更新版本重新生成。新版会在写 XML 前按最终 WAV sample length 缩放 marker，并同步 `SampleLength` / `End`。

## 文档

- [中文使用指南](docs/User_Guide_ZH.md)
- [English User Guide](docs/User_Guide_EN.md)
- [中文 Marker 参考](docs/Marker_Reference_ZH.md)
- [English Marker Reference](docs/Marker_Reference_EN.md)
- [第三方许可说明](docs/THIRD_PARTY_LICENSES.md)

## 注意事项

- 本工具不会分发 Fmod Bank Tools，也不会内置游戏文件。
- 替换游戏文件前请确认已有备份。
- 自动 marker / loop 分析只作为候选结果，最终仍建议人工试听确认。
- 推荐使用短路径，避免外部工具遇到路径或权限问题，例如：

  ```text
  E:\FH6RadioTool
  E:\FH6Music
  E:\FmodBankTools
  ```
