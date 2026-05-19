# FH6 Radio Tool v2.6.2 中文使用教程

## 1. 准备工作

请先准备：

- Python 3.10 或更新版本。
- FH6 游戏本体目录。
- 你要替换进游戏的音乐文件，建议使用 WAV。
- Fmod Bank Tools。FH6 Radio Tool 不内置该工具，需要用户自行下载并在界面中选择 `Fmod_Bank_Tools.exe`。

建议把工具和音乐放在简单路径中，例如：

```text
E:\FH6RadioTool
E:\Music
E:\FmodBankTool
```

尽量避免过长路径、特殊符号和权限受限目录。

## 2. 安装环境

第一次使用时，双击：

```text
setup_env.bat
```

该脚本会创建本地 `.venv` 环境，并安装所需依赖。它不会修改你的全局 Python 环境。

安装完成后，双击：

```text
run_tool.bat
```

## 3. 设置路径

打开工具后，设置栏默认显示。你需要选择：

1. 游戏根目录。
2. 音乐目录。
3. 界面语言。
4. 游戏语言 / Game XML。

选择游戏根目录后，工具会自动扫描游戏目录，并自动选择对应语言的 `RadioInfo*.xml`。切换游戏语言后，工具也会尝试重新选择对应 XML。

设置完成后，设置栏会自动隐藏，也可以通过左侧的设置标签重新显示。

## 4. 步骤 1：选择电台与歌曲

在“步骤 1 · 选择电台与歌曲”中：

1. 选择目标电台。
2. 在左侧列表中勾选需要被替换的游戏原曲槽位。
3. 在右侧列表中勾选你自己的音乐文件。
4. 左右两侧勾选数量需要一致。
5. 点击“应用选择替换”。

如果需要批量替换，直接勾选多个槽位和等量音乐即可。工具会按列表顺序配对。

## 5. 步骤 2：设置循环点

在“步骤 2 · 设置循环点”中：

1. 选择音频文件。
2. 点击“分析 Loop”生成候选循环段。
3. 在候选段落下拉框中选择不同候选。
4. 点击“试听候选”检查衔接效果。
5. 可使用“场景试听”模拟漫游、比赛循环、冲线后循环等场景。
6. 使用进度条和 Marker 区域进行手动微调。
7. 点击保存当前音频设置。

如果自动候选不理想，可以手动拖动进度条定位，再把当前点写入对应 Marker。

## 6. 步骤 3：生成或替换

在“步骤 3 · 生成或替换”中，先选择 Fmod Bank Tools 的 exe 路径。

然后选择一种输出方式：

### 仅生成 Mod 输出包

该模式不会覆盖游戏文件。工具会在 `output` 目录中生成修改后的 XML 和重打包后的 bank 文件。适合发布前检查或手动替换。

### 一键替换到游戏

该模式会自动：

```text
准备 Fmod Bank Tools 工作目录
→ Extract 目标 bank
→ 生成修改后的 WAV/XML
→ Rebuild bank
→ 备份原始 XML 和 bank
→ 覆盖游戏文件
```

一键替换前工具会创建备份 manifest。若出现问题，可以使用“恢复默认文件”恢复。

## 7. 关于 Fmod Bank Tools

FH6 Radio Tool 不直接包含 Fmod Bank Tools。你需要在工具里选择它的 `Fmod_Bank_Tools.exe`。

如果一键流程中 Fmod Bank Tools 弹出窗口，请不要在 Extract/Rebuild 过程中手动关闭它。若自动点击失败，请确认已经运行过 `setup_env.bat`，并且 `pywinauto` 已正确安装。

## 8. 常见问题

### 提示没有找到 bank 文件

请检查是否选择了正确的游戏根目录，以及 Fmod Bank Tools exe 路径是否正确。

### CU1 bank 提示没有 FSB 音频

部分 `R*_Tracks_CU1.assets.bank` 可能没有 Fmod Bank Tools 可提取的 FSB 音频。工具会尝试自动切换到同电台可提取的 bank，并在日志中说明。

### 一键替换失败

请查看右侧运行日志。不要手动复制不完整输出到游戏目录。可以先使用“仅生成 Mod 输出包”进行测试。

### XML 加载失败

请优先使用游戏原始 XML，不要使用已经被其他工具损坏或手动编辑出错的 XML。

## 9. 备份与恢复

点击“备份当前游戏文件”可以手动创建备份。

点击“恢复默认文件”会根据备份 manifest 恢复原始 XML 和 bank。建议每次大规模替换前都先备份。

## 批量导入 Marker 参数

v2.7.2 起支持从 CSV 或 JSON 文件批量导入每首歌的 Marker 参数。

推荐使用 CSV 格式。模板文件位于：

- `docs/examples/marker_import_template.csv`

本次也已经把提供的歌曲采样表整理为可导入示例：

- `docs/examples/marker_import_from_uploaded_song_samples.csv`

使用方法：

1. 先选择并扫描音乐目录。
2. 进入“设置循环点”步骤。
3. 在 Marker 参数区域点击“批量导入 Marker”。
4. 选择 CSV 或 JSON 文件。
5. 工具会优先按文件名 / 显示名匹配音乐文件；如果名称无法匹配，会尝试使用唯一的 SampleLength 匹配。

CSV 推荐字段：

`MatchName, Filename, DisplayName, Artist, SampleRate, SampleLength, TrackStart, TrackDrop, TrackLoopStart, TrackLoopEnd, PostDrop, PostRaceLoopStart, PostRaceLoopEnd, DJSegment, StingerStart, DJStart, End`

注意：导入后仍建议逐首试听确认，尤其是 TrackLoop 和 PostRaceLoop 的衔接效果。

## v2.7.3 补充说明

- 在“选择电台与歌曲”步骤中，右侧自己的音乐列表新增 Artist 列，可直接编辑歌手名。
- 编辑后的 Artist 会按歌曲保存，并在生成 XML 时用于游戏内显示。
- “导出导入模板”已改名为“导出 Marker”。
