# FH6 Radio Tool 使用指南

## 基本流程

1. 选择 `RadioInfo_CN.xml`。
2. 选择音乐文件夹。
3. 选择电台并校验音频。
4. 点击“① 歌名表”，编辑 `output/track_metadata.csv` 中的 `display_name` 和 `artist`。
5. 用 Fmod Bank Tools 对原 bank 执行 Extract。
6. 点击“② 导入 Extract”，选择 Fmod 的 wav 输出目录。
7. 点击“③ 最终生成”。

## 最终 output 目录

正式运行结束后，`output/` 只保留：

```text
output/
  RadioInfo_CN.xml
  track_metadata.csv
  fmod_ready_wav/
```

其中：

- `RadioInfo_CN.xml`：替换到游戏目录的中文电台配置。
- `track_metadata.csv`：歌曲显示名和艺人编辑表。
- `fmod_ready_wav/`：已经完成映射、替换和音量匹配的重构音乐文件夹。

## Fmod Bank Tools 设置

在 Fmod Bank Tools 中：

```text
Wav Output Directory = output/fmod_ready_wav
```

其他目录可以按 Fmod Bank Tools 自己的习惯设置，也可以使用 `work/fmod_rebuild_workspace/` 下生成的 `bank/build/fsbcache`。
