from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from .runtime_tools import candidate_tool_paths, is_frozen_app

from .models import AudioPrepareRecord
from .wav_tools import GENERATED_NORM_SUFFIX, validate_wav


def find_ffmpeg(explicit_path: str | None = None) -> str:
    """返回可执行 ffmpeg 路径。

    查找顺序：
    1. 用户显式指定的 ffmpeg.exe；
    2. 系统 PATH 中的 ffmpeg；
    3. setup_env.bat 安装到 .venv 里的 imageio-ffmpeg 内置 ffmpeg。

    这样普通用户运行 setup_env.bat 后通常不需要再手动配置 PATH。
    """
    checked: list[str] = []

    if explicit_path:
        p = Path(explicit_path)
        checked.append(str(p))
        if p.exists() and p.is_file():
            return str(p)
        found = shutil.which(explicit_path)
        if found:
            return found
        raise FileNotFoundError(f"找不到指定的 FFmpeg: {explicit_path}")

    for p in candidate_tool_paths("ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"):
        checked.append(str(p))
        if p.exists() and p.is_file():
            return str(p)

    found = shutil.which("ffmpeg")
    if found:
        return found

    try:
        import imageio_ffmpeg  # type: ignore

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        checked.append(str(bundled))
        if bundled and Path(bundled).exists():
            return str(bundled)
    except Exception as exc:
        checked.append(f"imageio_ffmpeg failed: {exc}")

    if is_frozen_app():
        hint = (
            "找不到 ffmpeg。当前便携 EXE 包不包含 setup_env.bat；"
            "请在工具中选择你自己的 ffmpeg.exe，或把 ffmpeg.exe 放到 EXE 同目录 / tools 目录 / 系统 PATH 后重试。\n"
        )
    else:
        hint = (
            "找不到 ffmpeg。这是源码运行环境缺少 FFmpeg；请在当前 v3 源码环境中安装 requirements.txt，"
            "或在工具中选择你自己的 ffmpeg.exe。\n"
        )
    raise FileNotFoundError(
        hint + "已检查位置：\n" + "\n".join(f"- {x}" for x in checked)
    )


def safe_stem(name: str, max_len: int = 96) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[^0-9a-zA-Z_\-\u4e00-\u9fff]+", "_", stem)
    stem = re.sub(r"_+", "_", stem).strip("_")
    return (stem or "track")[:max_len]


def normalized_wav_path_for(source: Path) -> Path:
    """不合规音频的规范化输出路径：写入原音乐文件夹同目录。"""
    return source.with_name(f"{safe_stem(source.name)}{GENERATED_NORM_SUFFIX}")


def run_ffmpeg_normalize(
    source: Path,
    output_path: Path,
    ffmpeg_path: str,
    expected_samplerate: int = 48000,
    expected_channels: int = 2,
    expected_bits: int = 16,
) -> None:
    if expected_bits != 16:
        raise ValueError("当前 FFmpeg 自动规范化原型只支持输出 16bit PCM WAV")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(source),
        "-vn",
        "-ac", str(expected_channels),
        "-ar", str(expected_samplerate),
        "-c:a", "pcm_s16le",
        str(output_path),
    ]

    creationflags = 0
    if sys.platform.startswith("win"):
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=creationflags,
    )

    if proc.returncode != 0:
        raise RuntimeError(
            "FFmpeg 转换失败：\n"
            f"命令: {' '.join(cmd)}\n"
            f"stderr:\n{proc.stderr.strip()}"
        )


def prepare_one_audio(
    source: Path,
    auto_normalize: bool = True,
    ffmpeg_path: str | None = None,
    expected_samplerate: int = 48000,
    expected_channels: int = 2,
    expected_bits: int = 16,
) -> AudioPrepareRecord:
    """把一个输入音频准备成最终可用于 patch 的 WAV。

    v0.4 策略：
    - 合规 WAV：原地使用，不复制、不改名，txt 写原文件名
    - 不合规音频：自动规范化为同目录 *_fh6_norm.wav，txt 写规范化文件名
    """
    source_validation = validate_wav(
        source,
        expected_samplerate=expected_samplerate,
        expected_channels=expected_channels,
        expected_bits=expected_bits,
    )
    source_info = source_validation.info

    if source_validation.ok and source_info is not None:
        return AudioPrepareRecord(
            source_path=source,
            status="ok",
            source_info=source_info,
            output_info=source_info,
            output_path=source,
            action="use_original",
            warnings=list(source_validation.warnings),
        )

    out_path = normalized_wav_path_for(source)

    if not auto_normalize:
        return AudioPrepareRecord(
            source_path=source,
            status="failed",
            source_info=source_info,
            output_path=out_path,
            action="skipped",
            errors=list(source_validation.errors),
            warnings=list(source_validation.warnings),
        )

    try:
        ffmpeg = find_ffmpeg(ffmpeg_path)
        run_ffmpeg_normalize(
            source,
            out_path,
            ffmpeg_path=ffmpeg,
            expected_samplerate=expected_samplerate,
            expected_channels=expected_channels,
            expected_bits=expected_bits,
        )

        normalized_validation = validate_wav(
            out_path,
            expected_samplerate=expected_samplerate,
            expected_channels=expected_channels,
            expected_bits=expected_bits,
        )

        if not normalized_validation.ok:
            return AudioPrepareRecord(
                source_path=source,
                status="failed",
                source_info=source_info,
                output_info=normalized_validation.info,
                output_path=out_path,
                action="ffmpeg",
                errors=[
                    "FFmpeg 已输出文件，但规范化结果仍不合规：",
                    *normalized_validation.errors,
                ],
                warnings=list(source_validation.warnings) + list(normalized_validation.warnings),
            )

        return AudioPrepareRecord(
            source_path=source,
            status="normalized",
            source_info=source_info,
            output_info=normalized_validation.info,
            output_path=out_path,
            action="ffmpeg",
            warnings=[
                "原文件不合规，已通过 FFmpeg 自动规范化。",
                *source_validation.errors,
                *source_validation.warnings,
                *normalized_validation.warnings,
            ],
        )

    except Exception as exc:
        return AudioPrepareRecord(
            source_path=source,
            status="failed",
            source_info=source_info,
            output_path=out_path,
            action="ffmpeg",
            errors=[str(exc)],
            warnings=list(source_validation.warnings),
        )
