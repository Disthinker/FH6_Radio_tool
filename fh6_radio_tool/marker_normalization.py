from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import AudioInfo, SegmentMarkers
from .segment_tools import MARKER_ORDER, NEGATIVE_SENTINEL_MARKERS, markers_from_json, markers_to_json


POST_LOOP_ALIASES = {
    "PostLoopStart": "PostRaceLoopStart",
    "PostLoopEnd": "PostRaceLoopEnd",
}


@dataclass(frozen=True)
class MarkerNormalizationResult:
    markers: SegmentMarkers
    final_sample_length: int
    source_sample_length: int | None
    prepared_sample_length: int
    scale: float
    marker_unit: str
    warnings: list[str]
    log_lines: list[str]


def _audio_name(info: AudioInfo | None) -> str:
    if info is None:
        return ""
    try:
        return Path(info.path).name
    except Exception:
        return info.filename


def _marker_source_length(source_audio_info: AudioInfo | None, source_sample_length: int | None) -> int | None:
    if source_sample_length and int(source_sample_length) > 0:
        return int(source_sample_length)
    if source_audio_info is not None and int(source_audio_info.sample_length or 0) > 0:
        return int(source_audio_info.sample_length)
    return None


def _marker_source_rate(source_audio_info: AudioInfo | None, source_sample_rate: int | None) -> int | None:
    if source_sample_rate and int(source_sample_rate) > 0:
        return int(source_sample_rate)
    if source_audio_info is not None and int(source_audio_info.samplerate or 0) > 0:
        return int(source_audio_info.samplerate)
    return None


def _raw_positions(markers: SegmentMarkers | dict[str, object] | None) -> dict[str, object]:
    if isinstance(markers, SegmentMarkers):
        raw: dict[str, object] = dict(markers.positions or {})
    elif isinstance(markers, dict):
        raw = dict(markers)
    else:
        raw = {}

    for alias, canonical in POST_LOOP_ALIASES.items():
        if alias in raw and canonical not in raw:
            raw[canonical] = raw[alias]
    return raw


def _convert_marker(
    name: str,
    raw_value: object,
    *,
    marker_unit: str,
    prepared_audio_info: AudioInfo,
    scale: float,
    source_rate: int | None,
    warnings: list[str],
) -> int | None:
    try:
        if marker_unit.lower() in {"second", "seconds", "sec", "s"}:
            value = round(float(raw_value) * prepared_audio_info.samplerate)
        else:
            value_i = int(float(raw_value))
            if value_i < 0:
                if name in NEGATIVE_SENTINEL_MARKERS and value_i == -1:
                    return -1
                return None
            if source_rate and source_rate > 0 and not scale:
                value = round((value_i / source_rate) * prepared_audio_info.samplerate)
            else:
                value = round(value_i * scale)
    except Exception:
        warnings.append(f"{name}: invalid marker value {raw_value!r}; skipped")
        return None

    end_sample = max(0, prepared_audio_info.sample_length - 1)
    if value < 0:
        if name in NEGATIVE_SENTINEL_MARKERS and value == -1:
            return -1
        return None
    if value > end_sample:
        warnings.append(f"{name}: {value} exceeds final range 0..{end_sample}; clamped")
        value = end_sample
    return max(0, int(value))


def normalize_track_markers_for_prepared_audio(
    markers: SegmentMarkers | dict[str, object] | None,
    source_audio_info: AudioInfo | None,
    prepared_audio_info: AudioInfo,
    *,
    source_sample_length: int | None = None,
    source_sample_rate: int | None = None,
    marker_unit: str = "samples",
    label: str = "",
) -> MarkerNormalizationResult:
    """Convert editable markers to the final prepared WAV sample coordinate system."""
    final_sample_length = max(0, int(prepared_audio_info.sample_length or 0))
    end_sample = max(0, final_sample_length - 1)
    raw = _raw_positions(markers)
    warnings: list[str] = []

    src_len = _marker_source_length(source_audio_info, source_sample_length)
    src_rate = _marker_source_rate(source_audio_info, source_sample_rate)
    if marker_unit.lower() in {"second", "seconds", "sec", "s"}:
        scale = 0.0
    elif src_len and src_len > 0:
        scale = float(final_sample_length) / float(src_len) if final_sample_length > 0 else 1.0
    elif src_rate and src_rate > 0 and prepared_audio_info.samplerate > 0:
        scale = 0.0
        warnings.append("source sample length missing; converted sample markers through seconds using source sample rate")
    else:
        scale = 1.0
        if raw:
            warnings.append("source sample length/rate missing; marker values kept in current sample coordinates")

    positions: dict[str, int] = {}
    for name in MARKER_ORDER:
        if name == "End":
            continue
        if name not in raw:
            continue
        value = _convert_marker(
            name,
            raw[name],
            marker_unit=marker_unit,
            prepared_audio_info=prepared_audio_info,
            scale=scale,
            source_rate=src_rate,
            warnings=warnings,
        )
        if value is not None:
            positions[name] = value

    positions.setdefault("TrackStart", 0)
    positions["End"] = end_sample

    for start_name, end_name in [
        ("TrackLoopStart", "TrackLoopEnd"),
        ("PostRaceLoopStart", "PostRaceLoopEnd"),
    ]:
        if start_name in positions and end_name in positions:
            if positions[end_name] >= 0 and positions[end_name] < positions[start_name]:
                warnings.append(f"{end_name}: before {start_name}; adjusted to loop start")
                positions[end_name] = positions[start_name]
            if positions[end_name] > end_sample:
                warnings.append(f"{end_name}: exceeds final sample length; clamped")
                positions[end_name] = end_sample

    normalized = markers_from_json(positions, prepared_audio_info)
    final_json = markers_to_json(normalized)
    prefix = f"{label}: " if label else ""
    scale_text = "seconds" if marker_unit.lower() in {"second", "seconds", "sec", "s"} else f"{scale:.9f}"
    log_lines = [
        f"[MARKER] {prefix}source_samples={src_len if src_len is not None else 'unknown'}, prepared_samples={final_sample_length}, marker_scale={scale_text}",
        f"[MARKER] {prefix}source={_audio_name(source_audio_info)}, prepared={_audio_name(prepared_audio_info)}",
        f"[MARKER] {prefix}final_values=" + ", ".join(f"{k}={v}" for k, v in final_json.items()),
    ]
    for warning in warnings:
        log_lines.append(f"[MARKER][WARN] {prefix}{warning}")

    return MarkerNormalizationResult(
        markers=normalized,
        final_sample_length=final_sample_length,
        source_sample_length=src_len,
        prepared_sample_length=final_sample_length,
        scale=scale,
        marker_unit=marker_unit,
        warnings=warnings,
        log_lines=log_lines,
    )
