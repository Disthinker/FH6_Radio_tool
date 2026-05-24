from __future__ import annotations

import re
import wave
from pathlib import Path

from .models import AudioInfo, AudioValidationResult


SUPPORTED_INPUT_EXTS = {
    ".wav", ".wave",
    ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".wma",
}

GENERATED_NORM_SUFFIX = "_fh6_norm.wav"


def natural_key(path: Path):
    """按人类习惯排序：track2.wav 会排在 track10.wav 前面。"""
    text = path.name
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", text)]


def is_generated_norm_file(path: Path) -> bool:
    return path.name.lower().endswith(GENERATED_NORM_SUFFIX)


def list_audio_candidates(folder: Path, exts: set[str] | None = None) -> list[Path]:
    if not folder.exists():
        raise FileNotFoundError(f"音乐文件夹不存在: {folder}")
    if not folder.is_dir():
        raise NotADirectoryError(f"不是文件夹: {folder}")

    exts = {x.lower() for x in (exts or SUPPORTED_INPUT_EXTS)}
    files: list[Path] = []
    for p in folder.iterdir():
        if not p.is_file():
            continue
        if p.suffix.lower() not in exts:
            continue
        if is_generated_norm_file(p):
            continue
        files.append(p)

    return sorted(files, key=natural_key)


def list_wavs(folder: Path) -> list[Path]:
    """保留旧接口：只列出非自动生成的 WAV。"""
    return list_audio_candidates(folder, {".wav", ".wave"})


def read_wav_info(path: Path) -> AudioInfo:
    try:
        with wave.open(str(path), "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            samplerate = wf.getframerate()
            frames = wf.getnframes()
    except wave.Error as exc:
        raise ValueError(f"无法作为标准 PCM WAV 读取: {exc}") from exc

    bits = sample_width * 8
    duration = frames / samplerate if samplerate else 0.0

    return AudioInfo(
        path=path,
        filename=path.name,
        samplerate=samplerate,
        channels=channels,
        bits_per_sample=bits,
        frames=frames,
        duration_sec=duration,
    )


def validate_wav(
    path: Path,
    expected_samplerate: int = 48000,
    expected_channels: int = 2,
    expected_bits: int = 16,
) -> AudioValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    try:
        info = read_wav_info(path)
    except Exception as exc:
        return AudioValidationResult(info=None, errors=[str(exc)], warnings=[])

    if info.frames <= 0:
        errors.append("音频帧数为 0")

    if info.samplerate != expected_samplerate:
        errors.append(
            f"采样率不符合要求: 当前 {info.samplerate} Hz, 期望 {expected_samplerate} Hz"
        )

    if info.channels != expected_channels:
        errors.append(
            f"声道数不符合要求: 当前 {info.channels}, 期望 {expected_channels}"
        )

    if info.bits_per_sample != expected_bits:
        errors.append(
            f"位深不符合要求: 当前 {info.bits_per_sample} bit, 期望 {expected_bits} bit"
        )

    if info.duration_sec < 5:
        warnings.append("音频时长小于 5 秒，可能不是完整歌曲")

    return AudioValidationResult(info=info, errors=errors, warnings=warnings)


def validate_wav_folder(
    folder: Path,
    expected_samplerate: int = 48000,
    expected_channels: int = 2,
    expected_bits: int = 16,
) -> list[AudioValidationResult]:
    results: list[AudioValidationResult] = []
    for wav in list_wavs(folder):
        results.append(
            validate_wav(
                wav,
                expected_samplerate=expected_samplerate,
                expected_channels=expected_channels,
                expected_bits=expected_bits,
            )
        )
    return results
