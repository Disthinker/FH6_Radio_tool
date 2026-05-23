# Marker 参数说明
以下说明基于当前 FH 电台 XML 工作流和社区测试经验。不同电台、不同 bank 的具体行为可能略有差异，因此建议在工具中试听，并最终进游戏测试。

### 常用播放 Marker

- **TrackStart**：歌曲正常开始播放的位置，通常为 `0`。
- **TrackLoopStart**：普通播放或比赛中循环段的起点。
- **TrackLoopEnd**：普通播放或比赛中循环段的终点。循环生效时，播放到这里会回到 `TrackLoopStart`。
- **PostRaceLoopStart**：赛后循环段起点。
- **PostRaceLoopEnd**：赛后循环段终点。赛后循环生效时，播放到这里会回到 `PostRaceLoopStart`。
- **End**：音频文件结束采样点，通常应为该音频最后一个有效采样点。

### 不太直观的 Marker

- **TrackDrop**：歌曲进入高能段落的位置，通常用于比赛开始或游戏需要切入歌曲高潮时。比较适合放在前奏之后的第一个副歌、Drop、主节拍或情绪最饱满的位置。如果歌曲没有明显 Drop，可以设置为 `TrackLoopStart` 或一个听感较强的位置。

- **PostDrop**：冲线或赛后切换时使用的高能入口点。比较适合放在副歌、最后一段 Drop 或冲线后听起来比较自然的高潮段。如果不确定，可以放在 `PostRaceLoopStart` 附近，或者复用一个稳定的副歌/Drop 位置。

- **DJSegment**：与 DJ / 电台插入段落相关的 Marker。普通自定义歌曲替换通常不需要使用。如果没有专门准备 DJ 片段，建议保持 `-1`。

- **DJStart**：DJ / 电台语音段落的开始点。普通音乐替换一般保持 `-1`，除非你明确准备了 DJ 或语音片段。

- **StingerStart**：短转场音效或 stinger 的起点。多数自定义歌曲不需要该参数。除非你知道歌曲中存在专门的转场音效，否则建议保持 `-1`。

### 实用设置建议

对大多数自定义歌曲，可以先按以下方式设置：

1. `TrackStart` 设置为 `0`。
2. `End` 设置为音频末尾采样点。
3. 选择稳定的副歌或主节拍循环段作为 `TrackLoopStart` / `TrackLoopEnd`。
4. `TrackDrop` 放在前奏之后第一个明显高潮、副歌或 Drop。
5. `PostDrop` 放在适合冲线后的高潮段，或者靠近 `PostRaceLoopStart`。
6. `PostRaceLoopStart` / `PostRaceLoopEnd` 选择一段适合赛后反复循环的音乐段落。
7. `DJSegment`、`DJStart`、`StingerStart` 普通情况下保持 `-1`。

自动 Loop 候选只能作为辅助参考，最终仍强烈建议手动试听和微调。
