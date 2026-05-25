from __future__ import annotations

import math
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .models import AudioPrepareRecord
from .runtime_tools import candidate_tool_paths, is_frozen_app
from .wav_tools import GENERATED_NORM_SUFFIX, read_wav_info, validate_wav


TARGET_GAME_DBFS = -18.0
DEFAULT_USER_GAIN_DB = 0.0
DEFAULT_GAIN_LIMIT_DB = 6.0
PEAK_HEADROOM_DBFS = -1.0


@dataclass(frozen=True)
class AudioNormalizationReport:
    source_path: Path
    output_path: Path
    source_format: str
    source_sample_rate: int | None
    source_channels: int | None
    source_duration_sec: float | None
    source_mean_dbfs: float | None
    source_peak_dbfs: float | None
    target_dbfs: float
    requested_gain_db: float
    applied_gain_db: float
    peak_limited: bool
    clipping_protection: str
    output_sample_rate: int
    output_channels: int
    output_bits: int
    status: str

    def source_sample_length_estimate(self) -> int | None:
        if self.source_sample_rate is None or self.source_duration_sec is None:
            return None
        if self.source_sample_rate <= 0 or self.source_duration_sec <= 0:
            return None
        return max(0, int(round(self.source_sample_rate * self.source_duration_sec)))


def find_ffmpeg(explicit_path: str | None = None) -> str:
    checked: list[str] = []

    if explicit_path:
        p = Path(explicit_path)
        checked.append(str(p))
        if p.exists() and p.is_file():
            return str(p)
        found = shutil.which(explicit_path)
        if found:
            return found
        raise FileNotFoundError(f"FFmpeg not found: {explicit_path}")

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
            "FFmpeg was not found. Choose ffmpeg.exe in the tool, or place "
            "ffmpeg.exe next to the EXE, in tools/, or on PATH."
        )
    else:
        hint = (
            "FFmpeg was not found. Install project requirements or choose "
            "ffmpeg.exe in the tool."
        )
    raise FileNotFoundError(hint + "\nChecked:\n" + "\n".join(f"- {x}" for x in checked))


def safe_stem(name: str, max_len: int = 96) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[^0-9a-zA-Z_\-\u4e00-\u9fff]+", "_", stem)
    stem = re.sub(r"_+", "_", stem).strip("_")
    return (stem or "track")[:max_len]


def normalized_wav_path_for(source: Path) -> Path:
    return source.with_name(f"{safe_stem(source.name)}{GENERATED_NORM_SUFFIX}")


def _null_sink() -> str:
    return "NUL" if sys.platform.startswith("win") else "/dev/null"


def _creationflags() -> int:
    if sys.platform.startswith("win"):
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _duration_from_text(text: str) -> float | None:
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    return int(match.group(1)) * 3600.0 + int(match.group(2)) * 60.0 + float(match.group(3))


def _channels_from_stream_text(text: str) -> int | None:
    if re.search(r"\bmono\b", text, re.IGNORECASE):
        return 1
    if re.search(r"\bstereo\b", text, re.IGNORECASE):
        return 2
    if re.search(r"\b5\.1\b", text):
        return 6
    if re.search(r"\b7\.1\b", text):
        return 8
    match = re.search(r"\b(\d+(?:\.\d+)?)\s+channels\b", text, re.IGNORECASE)
    if not match:
        return None
    try:
        return int(float(match.group(1)))
    except Exception:
        return None


def _probe_audio_with_ffmpeg(source: Path, ffmpeg_path: str) -> dict[str, object]:
    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-nostats",
        "-i",
        str(source),
        "-af",
        "volumedetect",
        "-f",
        "null",
        _null_sink(),
    ]
    proc = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=_creationflags(),
    )
    text = (proc.stderr or "") + "\n" + (proc.stdout or "")
    if proc.returncode != 0:
        return {
            "source_format": source.suffix.lower().lstrip(".") or "unknown",
            "sample_rate": None,
            "channels": None,
            "duration": None,
            "mean_volume": None,
            "max_volume": None,
            "status": "probe_failed",
            "stderr": text.strip(),
        }

    stream_line = ""
    for line in text.splitlines():
        if "Audio:" in line:
            stream_line = line
            break

    sample_rate = None
    match = re.search(r"(\d+)\s*Hz", stream_line)
    if match:
        sample_rate = int(match.group(1))

    source_format = source.suffix.lower().lstrip(".") or "unknown"
    match = re.search(r"Audio:\s*([^,\s]+)", stream_line)
    if match:
        source_format = match.group(1).strip()

    mean_volume = None
    max_volume = None
    match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", text)
    if match:
        mean_volume = float(match.group(1))
    match = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", text)
    if match:
        max_volume = float(match.group(1))

    return {
        "source_format": source_format,
        "sample_rate": sample_rate,
        "channels": _channels_from_stream_text(stream_line),
        "duration": _duration_from_text(text),
        "mean_volume": mean_volume,
        "max_volume": max_volume,
        "status": "ok",
        "stderr": text.strip(),
    }


def _safe_gain_from_probe(
    mean_dbfs: float | None,
    peak_dbfs: float | None,
    *,
    target_dbfs: float,
    user_gain_db: float,
    gain_limit_db: float,
) -> tuple[float, float, bool, str]:
    requested = user_gain_db if mean_dbfs is None else (target_dbfs - mean_dbfs) + user_gain_db
    requested_clamped = max(-gain_limit_db, min(gain_limit_db, requested))
    applied = requested_clamped
    peak_limited = False
    clipping_status = "not_needed"

    if peak_dbfs is not None and applied > 0:
        max_safe_boost = PEAK_HEADROOM_DBFS - peak_dbfs
        if applied > max_safe_boost:
            applied = max_safe_boost
            peak_limited = True
            clipping_status = "limited_by_peak"

    if not math.isfinite(applied):
        applied = 0.0
    return requested, applied, peak_limited, clipping_status


def run_ffmpeg_normalize(
    source: Path,
    output_path: Path,
    ffmpeg_path: str,
    expected_samplerate: int = 48000,
    expected_channels: int = 2,
    expected_bits: int = 16,
    target_dbfs: float = TARGET_GAME_DBFS,
    user_gain_db: float = DEFAULT_USER_GAIN_DB,
    gain_limit_db: float = DEFAULT_GAIN_LIMIT_DB,
) -> AudioNormalizationReport:
    if expected_bits != 16:
        raise ValueError("FFmpeg normalization currently outputs 16-bit PCM WAV only.")

    source = Path(source)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    probe = _probe_audio_with_ffmpeg(source, ffmpeg_path)
    requested_gain, applied_gain, peak_limited, clipping_status = _safe_gain_from_probe(
        probe.get("mean_volume") if isinstance(probe.get("mean_volume"), float) else None,
        probe.get("max_volume") if isinstance(probe.get("max_volume"), float) else None,
        target_dbfs=target_dbfs,
        user_gain_db=user_gain_db,
        gain_limit_db=gain_limit_db,
    )

    cmd = [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-vn",
        "-af",
        f"volume={applied_gain:.6f}dB,aresample={expected_samplerate}",
        "-ac",
        str(expected_channels),
        "-ar",
        str(expected_samplerate),
        "-sample_fmt",
        "s16",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    proc = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=_creationflags(),
    )

    if proc.returncode != 0:
        raise RuntimeError(
            "FFmpeg conversion failed:\n"
            f"command: {' '.join(cmd)}\n"
            f"stderr:\n{proc.stderr.strip()}"
        )

    output_info = read_wav_info(output_path)
    return AudioNormalizationReport(
        source_path=source,
        output_path=output_path,
        source_format=str(probe.get("source_format") or source.suffix.lower().lstrip(".") or "unknown"),
        source_sample_rate=probe.get("sample_rate") if isinstance(probe.get("sample_rate"), int) else None,
        source_channels=probe.get("channels") if isinstance(probe.get("channels"), int) else None,
        source_duration_sec=probe.get("duration") if isinstance(probe.get("duration"), float) else None,
        source_mean_dbfs=probe.get("mean_volume") if isinstance(probe.get("mean_volume"), float) else None,
        source_peak_dbfs=probe.get("max_volume") if isinstance(probe.get("max_volume"), float) else None,
        target_dbfs=float(target_dbfs),
        requested_gain_db=float(requested_gain),
        applied_gain_db=float(applied_gain),
        peak_limited=bool(peak_limited),
        clipping_protection=clipping_status,
        output_sample_rate=output_info.samplerate,
        output_channels=output_info.channels,
        output_bits=output_info.bits_per_sample,
        status="standardized",
    )


def describe_audio_normalization_report(report: AudioNormalizationReport | None) -> list[str]:
    if report is None:
        return []
    source_sr = "" if report.source_sample_rate is None else f"{report.source_sample_rate} Hz"
    source_ch = "" if report.source_channels is None else str(report.source_channels)
    source_dur = "" if report.source_duration_sec is None else f"{report.source_duration_sec:.3f}s"
    mean = "" if report.source_mean_dbfs is None else f"{report.source_mean_dbfs:.2f} dBFS"
    peak = "" if report.source_peak_dbfs is None else f"{report.source_peak_dbfs:.2f} dBFS"
    return [
        f"Source format={report.source_format}, sample_rate={source_sr}, channels={source_ch}, duration={source_dur}",
        f"Source loudness={mean}, peak={peak}, target={report.target_dbfs:.1f} dBFS",
        (
            f"Applied gain={report.applied_gain_db:.2f} dB "
            f"(requested {report.requested_gain_db:.2f} dB), "
            f"peak_limited={report.peak_limited}, clipping_protection={report.clipping_protection}"
        ),
        f"Prepared WAV={report.output_sample_rate} Hz, channels={report.output_channels}, bits={report.output_bits}",
    ]


def prepare_one_audio(
    source: Path,
    auto_normalize: bool = True,
    ffmpeg_path: str | None = None,
    expected_samplerate: int = 48000,
    expected_channels: int = 2,
    expected_bits: int = 16,
) -> AudioPrepareRecord:
    source = Path(source)
    source_validation = validate_wav(
        source,
        expected_samplerate=expected_samplerate,
        expected_channels=expected_channels,
        expected_bits=expected_bits,
    )
    source_info = source_validation.info

    if source_validation.ok and source_info is not None and not auto_normalize:
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
        report = run_ffmpeg_normalize(
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
                    "FFmpeg produced a file, but the normalized WAV still failed validation.",
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
                "Audio standardized with FFmpeg for FH6: PCM WAV, 48000 Hz, stereo, 16-bit.",
                *describe_audio_normalization_report(report),
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
