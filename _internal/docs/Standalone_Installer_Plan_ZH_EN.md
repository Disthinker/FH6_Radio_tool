# Standalone / Installer Plan Deferred

## 中文

v2.7.14 不再把 Nuitka standalone 作为正式发布路径。当前正式发布方式是：

```text
setup_env.bat + run_tool.bat + build_portable_release.bat
```

原因：此前 standalone 探索在虚拟机、杀毒软件、pywinauto、Fmod Bank Tools GUI 自动化、FSBank Rebuild 配置之间出现不稳定组合。为了避免普通玩家拿到不可靠的 exe，正式版本回退到稳定的批处理发布包。

后续若继续研究 standalone，应单独开实验分支，不影响主工具功能迭代。只有在干净 Windows 环境完整验证以下链路后，才考虑重新作为正式发布选项：启动 UI、扫描游戏、扫描音乐、imageio-ffmpeg 转码、Fmod Bank Tools Extract、生成 WAV/XML、Fmod Bank Tools Rebuild、备份、覆盖与恢复。

## English

v2.7.14 no longer treats Nuitka standalone as the official release path. The official release path is now:

```text
setup_env.bat + run_tool.bat + build_portable_release.bat
```

The previous standalone exploration showed unstable interactions between virtual machines, antivirus software, pywinauto, Fmod Bank Tools GUI automation, and FSBank Rebuild settings. To avoid giving normal players an unreliable executable, the release path is rolled back to the stable batch-based package.

Future standalone work should happen in a separate experimental branch. It should not block normal feature fixes. It should only become an official release option after the full workflow is validated on a clean Windows machine: UI launch, game scan, music scan, imageio-ffmpeg conversion, Fmod Bank Tools Extract, WAV/XML generation, Fmod Bank Tools Rebuild, backup, deployment, and restore.
