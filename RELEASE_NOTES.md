# FH6 Radio Tool v2.7.3 Release Notes

## English

- Fixed the Loop Candidate / Scene Preview layout where controls could become visually separated after recent UI changes.
- Fixed the custom music table header mismatch after switching UI language. The Artist column now stays correctly between File name and Format.
- Kept the v2.7.2 Rebuild waiting fix and the v2.7.x Marker import / editable Artist features.

## 中文

- 修复 Loop 候选与场景试听区域在新版界面中出现控件割裂、布局不连贯的问题。
- 修复切换界面语言后，自定义音乐表格表头与实际内容错位的问题；Artist 列现在会正确位于“文件名”和“格式”之间。
- 保留 v2.7.2 的 Rebuild 等待修复，以及 v2.7.x 的 Marker 批量导入和可编辑 Artist 功能。

---

# FH6 Radio Tool v2.7.3 Release Notes

## English

This is a small usability update based on v2.7.0.

### Changes

- Improved the Marker parameter layout so each label is placed directly next to its input box.
- Renamed "Export marker template" to "Export markers".
- Added an editable **Artist** column in the user music list.
- The custom Artist value is saved per track and used for in-game display when generating XML.
- Kept CSV / JSON batch Marker import support from v2.7.0.

## 中文

这是基于 v2.7.0 的小幅易用性更新。

### 更新内容

- 优化 Marker 参数布局，使每个参数名和输入框一一对应显示。
- 将“导出导入模板”改名为“导出 Marker”。
- 在自定义音乐列表中新增可编辑的 **Artist** 列。
- 用户填写的 Artist 会按歌曲保存，并在生成 XML 时用于游戏内显示。
- 保留 v2.7.0 的 CSV / JSON 批量导入 Marker 功能。


## v2.7.3

- Fixed one-click workflow sometimes waiting forever after Fmod Bank Tools Rebuild finished.
- Rebuild detection now ignores stale build outputs from previous runs and requires fresh or updated bank files.
- Added retry/verification when cleaning the external Fmod Bank Tools build directory.
- If Fmod Bank Tools is closed after output is already stable, the workflow can continue safely.
