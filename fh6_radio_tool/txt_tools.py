from __future__ import annotations

from pathlib import Path

from .models import TrackPatch


def write_bank_wav_list(tracks: list[TrackPatch], out_path: Path) -> None:
    """生成给后续 bank 工具使用的 WAV 清单。

    v0.4 规则：
    - 合规文件：写原文件名
    - 自动规范化文件：写 *_fh6_norm.wav 文件名
    - out_path 默认位于音乐文件夹中，保证 txt 与实际音频文件处在同一目录
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(t.audio.filename for t in tracks)
    if content:
        content += "\n"
    out_path.write_text(content, encoding="utf-8")


def read_bank_wav_list(path: Path) -> list[str]:
    """读取 bank_wav_list.txt，忽略空行和 # 注释。"""
    items: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        items.append(line)
    return items
