# FH6 Radio Tool

> 给游戏电台换歌用的小工具。  
> 目标：少点手工活，少点玄学错位，少点“我明明换了怎么还是原歌”的血压时刻。

FH6 Radio Tool 是一个轻量级桌面工具，用来辅助制作自定义游戏电台音乐。它可以帮你修改 `RadioInfo_CN.xml`，生成歌曲显示名表，导入 Fmod Bank Tools 的 Extract 结果，自动匹配 `sound_0.wav / sound_1.wav` 这类无语义音频文件，并生成可用于 Rebuild 的 `fmod_ready_wav` 文件夹。

简单说：  
你负责准备音乐和点按钮，工具负责处理 XML、映射、替换和基础音量匹配。  
最后 bank 仍然用 Fmod Bank Tools 重构。

## 这个工具适合谁

适合：

- 想把游戏电台换成自己歌单的玩家；
- 不想手动改一堆 XML 字段的玩家；
- 被“歌名显示 A，实际播放 B”折磨过的玩家；
- 会用 Fmod Bank Tools，但不想手动对 `sound_0.wav` 的玩家。

不适合：

- 完全不想碰 Fmod Bank Tools 的用户；
- 想一键直接改游戏文件的用户；
- 想让工具帮你准备游戏原始 bank 或版权音乐的用户。

## 鸣谢

本工具的制作参考了 B 站相关教程：

- https://www.bilibili.com/opus/748254891671027745?from=search&spm_id_from=333.337.0.0

感谢教程作者把地平线电台替换流程整理出来。没有前人的踩坑记录，我们大概率还在 `sound_114514.wav` 里迷路。

本工具也依赖 Fmod Bank Tools 的工作流：

- https://github.com/Wouldubeinta/Fmod-Bank-Tools.git

感谢 Fmod Bank Tools 仓库及其作者 Wouldubeinta。bank 的 Extract / Rebuild 仍然由 Fmod Bank Tools 完成，本工具只是把中间最容易出错的映射、替换、音量匹配过程自动化。

## 功能特性

- 读取 `RadioInfo_CN.xml`；
- 选择目标电台；
- 校验 WAV 音频；
- 生成 `track_metadata.csv`，用于编辑歌曲显示名和 artist；
- 导入 Fmod Bank Tools Extract 后的 wav 输出目录；
- 用 `SampleLength / SampleRate` 自动匹配原 bank 中的 `sound_x.wav`；
- 自动平行替换音频内容，避免歌名和实际播放错位；
- 自动做基础音量匹配；
- 输出目录保持简洁；
- 使用本地 `.venv` 虚拟环境，不污染全局 Python；
- 提供卸载脚本清理环境。

## 下载和启动

### 1. 安装环境

双击：

```text
安装环境.bat
```

脚本会创建本地虚拟环境：

```text
.venv/
```

并默认使用国内 PyPI 镜像：

```text
清华镜像 -> 阿里云镜像 -> 官方 PyPI
```

哪个能用就用哪个，不需要你手动研究 pip 代理。

### 2. 启动工具

双击：

```text
启动工具.bat
```

如果启动失败，先重新运行一次 `安装环境.bat`。

## 快速流程

```text
1. 选择 RadioInfo_CN.xml
2. 选择你的音乐文件夹
3. 选择电台并校验音频
4. 点击 ① 歌名表，编辑 output/track_metadata.csv
5. 用 Fmod Bank Tools Extract 原 bank
6. 点击 ② 导入 Extract
7. 点击 ③ 最终生成
8. 在 Fmod Bank Tools 中用 output/fmod_ready_wav 重构 bank
9. 替换游戏里的 XML 和新 bank
```

## 最终 output 目录

运行结束后，`output/` 只保留：

```text
output/
  RadioInfo_CN.xml
  track_metadata.csv
  fmod_ready_wav/
```

| 文件 / 文件夹 | 用途 |
|---|---|
| `RadioInfo_CN.xml` | 最终替换到游戏目录的 XML |
| `track_metadata.csv` | 歌名 / artist 编辑表 |
| `fmod_ready_wav/` | 已经完成映射、替换、音量匹配的重构音频目录 |

普通用户只需要关注 `output/`。  
`work/` 是调试和中间文件目录，不懂也没关系，别乱删到一半就行。

## Fmod Bank Tools 怎么用

你仍然需要 Fmod Bank Tools：

- GitHub 仓库：https://github.com/Wouldubeinta/Fmod-Bank-Tools.git

简化理解：

```text
Extract：把原 bank 拆成 wav + txt
Rebuild：用 wav + txt 重新打包成 bank
```

在本工具流程里：

1. 先用 Fmod Bank Tools 对原 bank 执行 Extract；
2. 回到本工具导入 Extract 的 wav 输出目录；
3. 本工具生成 `output/fmod_ready_wav/`；
4. 再回 Fmod Bank Tools，把 `Wav Output Directory` 指向 `output/fmod_ready_wav`；
5. 执行 Rebuild；
6. 拿 build 里的新 bank 去替换游戏文件。

详细步骤请看：

```text
docs/详细使用指南.md
docs/Fmod_Bank_Tools_简明教程.md
```

## 卸载

双击：

```text
卸载环境.bat
```

可删除：

- `.venv/`
- Python 缓存；
- 可选删除 `output/`、`work/`、`backup/`。

如果 `.venv` 删除失败，大概率是工具窗口或 Python 进程还开着。关掉后再运行一次。

## 常见问题

### 歌名变了，但音乐没变

XML 生效了，但 bank 没替换成功。  
请确认你放回游戏目录的是 Rebuild 后的新 bank，不是原 bank。

### 歌名和实际播放音乐错位

请确认你导入的是目标电台对应 bank 的 Extract wav 输出目录。然后重新点击 `③ 最终生成`。

### Extract 出来全是 sound_0.wav，正常吗？

正常。  
这也是为什么工具要用 `SampleLength / SampleRate` 自动匹配，而不是靠文件名猜。

### 音量会自动平衡吗？

会。  
工具会按原 extracted wav 的活跃 RMS 做基础音量匹配。不是专业母带处理，但能减少“下一首突然炸耳”的情况。

## 版权和使用说明

本工具仅用于学习、研究和个人自定义用途。

请不要随包分发：

- 游戏原始 bank；
- 游戏原始 XML；
- 受版权保护的音乐；
- 任何侵犯版权的资源。

用户应自行确认使用方式符合所在地法律、平台规则和游戏 EULA。


## 批处理乱码说明

v3.6.1 起，批处理文件已调整为：

```text
UTF-8 无 BOM
CRLF 换行
开头执行 chcp 65001
正文尽量使用 ASCII 文本
```

如果你的系统环境中中文 `.bat` 文件显示仍然不正常，可以使用英文别名：

```text
setup_env.bat      安装环境
run_tool.bat       启动工具
cleanup_env.bat    卸载环境
```

中文 bat 和英文 bat 功能完全相同。


## v3.6.2 修复

修复试听进度条可能提前到达末尾的问题。

原因是部分 WAV 文件或 Qt 多媒体后端返回的播放器 duration 可能短于 WAV header 中的真实采样长度。旧版本使用播放器 duration 设置进度条范围，可能导致：

```text
进度条拖到最右边
但音乐实际还没播放到结尾
写入 End marker 过早
游戏内自定义音乐提前结束
```

v3.6.2 起，试听进度条改为直接使用 WAV 的真实 sample/frame 数定位，不再使用播放器返回的 duration 作为滑条范围。

如果你之前已经保存过错误的 End，请重新打开对应音乐，把进度条拖到最右侧，并重新写入 `End`。


## v3.6.3 修复

修复自定义音乐在游戏内可能提前结束的问题。

v3.6.2 只修复了试听进度条的采样点定位，但没有覆盖另一类更关键的问题：部分 `RadioInfo_CN.xml` 会把 `TrackStart`、`End`、`TrackLoopStart` 等字段作为 `Sample` 属性保存，例如：

```xml
<Sample TrackStart="0" End="123456" ... />
```

旧版工具只写入了子节点形式：

```xml
<Marker Name="End" Position="999999" />
```

如果游戏实际读取的是属性 `End`，那么旧的 `End` 仍然会生效，导致新歌提前结束。

v3.6.3 起，工具会同时写入：

```text
Sample 属性形式：End="..."
Marker 子节点形式：<Marker Name="End" Position="..." />
```

如果你之前已经生成过提前结束的 XML，请重新点击 `③ 最终生成`。如果你曾手动保存过错误 End，请重新把进度条拖到最右侧并写入一次 End。


## v3.6.4 修复

修复工具内“试听与段落设置”阶段自定义音乐预览可能提前结束的问题。

旧版本使用 Qt `QMediaPlayer` 预览 WAV。部分 Windows 多媒体后端对 WAV 的 duration / seek / EOF 处理可能不稳定，表现为：

```text
工具显示的时长看起来正确
但试听拖动或播放时会提前结束
导致用户无法可靠设置 End / Loop 等 Marker
```

v3.6.4 起，试听模块改为内置 PCM16 WAV 播放器：

```text
直接读取 WAV frame
直接按 sample 位置播放和拖动
进度条 value = 真实 sample position
不再依赖 QMediaPlayer duration / position
```

因此，“试听进度条末尾”和“WAV 真实末尾”现在是一致的。


## v3.7 双语界面

v3.7 新增中英文界面切换。

在工具顶部可以选择：

```text
中文
English
```

切换后会更新：

- 左侧操作向导；
- 主要按钮；
- 分组标题；
- 音频校验表头；
- Marker 简短说明；
- 常用弹窗提示。

核心功能不变，仍然保留：

- SampleLength 自动映射；
- sound_x.wav 平行替换；
- 基础音量匹配；
- 内置 WAV 试听播放器。
