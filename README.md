# FH6 Radio Tool

FH6 Radio Tool 是一个轻量级游戏电台音乐替换辅助工具。它面向想要自定义电台音乐的普通玩家，目标是把复杂的 XML 修改、音频映射、音量匹配和 Fmod Bank Tools 重构准备工作尽量自动化。

> 当前版本：v1.0  
> 适用场景：自定义游戏电台音乐、修改游戏内歌曲显示名、生成可用于 Fmod Bank Tools Rebuild 的音频目录。

## 功能特性

- 读取并修改 `RadioInfo_CN.xml`；
- 选择目标电台；
- 校验 WAV 音频；
- 生成 `track_metadata.csv`，用于编辑歌曲显示名和 artist；
- 导入 Fmod Bank Tools Extract 后的 wav 输出目录；
- 使用 `SampleLength / SampleRate` 自动匹配原 bank 中的 `sound_x.wav`；
- 自动平行替换音频内容，避免歌名和实际音乐错位；
- 自动做基础音量匹配；
- 最终 output 目录保持简洁；
- 使用本地 `.venv` 虚拟环境，不污染全局 Python；
- 提供卸载脚本清理环境。

## 项目边界

本工具负责：

```text
XML 配置修改
音频校验
歌名表生成
映射校准
音频平行替换
音量匹配
生成 fmod_ready_wav
```

本工具不负责：

```text
直接生成 bank
直接修改游戏目录
分发游戏原始 bank
分发受版权保护的音乐文件
```

bank 重构仍由 Fmod Bank Tools 完成。

## 快速开始

### 1. 安装环境

双击：

```text
安装环境.bat
```

安装脚本会创建本地虚拟环境：

```text
.venv/
```

并默认使用国内 PyPI 镜像安装依赖：

```text
清华 PyPI 镜像
阿里云 PyPI 镜像
官方 PyPI
```

如果一个源失败，会自动尝试下一个源。

### 2. 启动工具

双击：

```text
启动工具.bat
```

### 3. 基本流程

```text
1. 选择 RadioInfo_CN.xml
2. 选择音乐文件夹
3. 选择电台并校验音频
4. 点击 ① 歌名表，编辑 output/track_metadata.csv
5. 用 Fmod Bank Tools Extract 原 bank
6. 点击 ② 导入 Extract
7. 点击 ③ 最终生成
8. 在 Fmod Bank Tools 中使用 output/fmod_ready_wav 重构 bank
9. 替换游戏中的 XML 和新 bank
```

## 最终 output 结构

运行结束后，`output/` 只保留：

```text
output/
  RadioInfo_CN.xml
  track_metadata.csv
  fmod_ready_wav/
```

说明：

| 路径 | 作用 |
|---|---|
| `output/RadioInfo_CN.xml` | 最终需要替换到游戏目录的 XML |
| `output/track_metadata.csv` | 歌曲显示名 / artist 编辑表 |
| `output/fmod_ready_wav/` | 已完成映射、替换、音量匹配的重构音频目录 |

## Fmod Bank Tools 设置

在 Fmod Bank Tools 中，将：

```text
Wav Output Directory
```

设置为：

```text
output/fmod_ready_wav
```

然后使用原 bank 执行 Rebuild。

生成的新 bank 与 `output/RadioInfo_CN.xml` 一起替换到游戏实际读取的位置。

## 目录说明

```text
.
├─ fh6_radio_tool/          主程序
├─ docs/                    文档
├─ output/                  最终输出
├─ work/                    内部调试与中间文件
├─ backup/                  XML 备份
├─ requirements.txt         Python 依赖
├─ 安装环境.bat
├─ 启动工具.bat
└─ 卸载环境.bat
```

普通用户主要关注：

```text
output/
```

开发或排错时才需要看：

```text
work/
```

## 卸载

运行：

```text
卸载环境.bat
```

它会删除：

- `.venv/`
- `__pycache__/`
- `*.pyc`

并可选删除：

- `output/`
- `work/`
- `backup/`

如果 `.venv` 删除失败，请关闭工具窗口，或在任务管理器中结束 `python.exe/pythonw.exe` 后再运行。

## 详细指南

见：

```text
docs/详细使用指南.md
```

## 常见问题

### 歌名变了，但音乐没变

XML 已生效，但 bank 没替换成功。请确认你放回游戏目录的是 Fmod Bank Tools Rebuild 后的新 bank，而不是原 bank。

### 歌名和实际播放音乐错位

请确认导入的是目标电台对应 bank 的 Fmod Extract wav 输出目录，然后重新点击 `③ 最终生成`。

### pip 安装依赖很慢或失败

`安装环境.bat` 默认使用国内镜像。如果仍失败，可以检查网络，或手动执行：

```bat
.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 许可证与版权说明

本工具仅用于学习、研究和个人自定义用途。

请勿分发：

- 游戏原始 bank；
- 游戏原始 XML；
- 受版权保护的音乐文件；
- 任何侵犯他人版权的资源。

用户应自行确认其使用方式符合所在地法律、平台规则和游戏 EULA。
