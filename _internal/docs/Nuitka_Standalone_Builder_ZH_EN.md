# v2.7.25 - Nuitka build stability hotfix

## 中文

本版本只针对 Nuitka 打包流程做稳定性修复，不改变游戏替换逻辑。

修复点：

- `build_nuitka_standalone.bat` 会先关闭旧的 `FH6RadioTool.exe`，避免旧进程锁定输出文件。
- Debug 构建改用 `--windows-console-mode=force`，比 `attach` 更适合虚拟机调试构建。
- Nuitka 输出目录改到 `%LOCALAPPDATA%\FH6RadioTool\NuitkaBuild\v2.7.25`，构建完成后再复制到项目目录 `dist_nuitka_debug`。
- 默认禁用 ccache/clcache：`--disable-cache=ccache`，减少缓存/杀毒软件/资源写入之间的锁文件问题。
- 如果以管理员身份运行，脚本会尝试给项目目录、本地构建目录和临时目录添加 Windows Defender 排除项。
- 如果仍然遇到 `Failed to add resources to file`，脚本会给出明确提示：右键 BAT，以管理员身份运行一次。

## English

This version only improves the Nuitka build workflow. It does not change the game replacement logic.

Changes:

- The builder closes old `FH6RadioTool.exe` processes before building.
- Debug builds now use `--windows-console-mode=force`, which is more stable for VM/debug builds than `attach`.
- Nuitka now builds under `%LOCALAPPDATA%\FH6RadioTool\NuitkaBuild\v2.7.25`, then copies the finished `.dist` back to `dist_nuitka_debug`.
- ccache/clcache is disabled with `--disable-cache=ccache` to reduce cache/antivirus/resource-update locking issues.
- When run as administrator, the builder attempts to add Windows Defender exclusions for the project, local build, and temp directories.
- If `Failed to add resources to file` still appears, right-click the BAT and run it as administrator once.
