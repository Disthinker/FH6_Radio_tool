from __future__ import annotations

import json
from pathlib import Path

from .models import AudioInfo, AudioPrepareRecord, SegmentMarkers


SEGMENTS_FILE_NAME = "segments.json"

# UI 与 XML 写入使用的 Marker 顺序。
MARKER_ORDER = [
    "TrackStart",
    "TrackDrop",
    "TrackLoopStart",
    "TrackLoopEnd",
    "PostDrop",
    "PostRaceLoopStart",
    "PostRaceLoopEnd",
    "DJSegment",
    "StingerStart",
    "DJStart",
    "End",
]

# 给普通用户看的简短说明。尽量避免过度技术化。
MARKER_DESCRIPTIONS = {
    "TrackStart": "歌曲开始",
    "TrackDrop": "比赛高潮点",
    "TrackLoopStart": "比赛循环起点",
    "TrackLoopEnd": "比赛循环终点",
    "PostDrop": "冲线/赛后高潮",
    "PostRaceLoopStart": "赛后循环起点",
    "PostRaceLoopEnd": "赛后循环终点",
    "DJSegment": "DJ 插入点",
    "StingerStart": "转场音效点",
    "DJStart": "DJ 开始点",
    "End": "歌曲结束",
}


def seconds_to_sample(seconds: float, sample_rate: int) -> int:
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    return max(0, int(round(seconds * sample_rate)))


def sample_to_seconds(sample: int, sample_rate: int) -> float:
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    return max(0, int(sample)) / sample_rate


def clamp_marker(value: int, max_sample: int) -> int:
    return max(0, min(int(value), max(0, int(max_sample))))


def default_markers_for_audio(audio: AudioInfo) -> SegmentMarkers:
    # 只强制默认 TrackStart / End。其他 Marker 默认 -1，不写入，避免无依据乱改。
    return SegmentMarkers({
        "TrackStart": 0,
        "End": max(0, audio.sample_length - 1),
    })


def markers_to_json(markers: SegmentMarkers) -> dict:
    data: dict[str, int] = {}
    for name in MARKER_ORDER:
        value = markers.positions.get(name)
        if value is None:
            continue
        if int(value) >= 0:
            data[name] = int(value)
    return data


def markers_from_json(data: dict, audio: AudioInfo | None = None) -> SegmentMarkers:
    max_sample = audio.sample_length - 1 if audio is not None else 2_147_483_647
    positions: dict[str, int] = {}

    # 兼容 v0.5/v1.2 旧字段。
    legacy_map = {
        "track_start": "TrackStart",
        "track_loop_start": "TrackLoopStart",
        "track_loop_end": "TrackLoopEnd",
        "end": "End",
    }
    for old, new in legacy_map.items():
        if old in data and new not in data:
            data[new] = data[old]

    for name in MARKER_ORDER:
        if name not in data:
            continue
        try:
            value = int(data[name])
        except Exception:
            continue
        if value < 0:
            continue
        positions[name] = clamp_marker(value, max_sample)

    # 基本合法性修正。
    if "TrackStart" not in positions:
        positions["TrackStart"] = 0
    if "End" not in positions:
        positions["End"] = max_sample
    if positions["End"] < positions["TrackStart"]:
        positions["End"] = positions["TrackStart"]

    for start_name, end_name in [
        ("TrackLoopStart", "TrackLoopEnd"),
        ("PostRaceLoopStart", "PostRaceLoopEnd"),
    ]:
        if start_name in positions and end_name in positions:
            if positions[end_name] < positions[start_name]:
                positions[end_name] = positions[start_name]

    return SegmentMarkers(positions)


def load_segments(path: Path) -> dict:
    if not path.exists():
        return {"version": 2, "items": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"segments.json 格式错误: {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("segments.json 顶层必须是 object")
    data.setdefault("version", 2)
    data.setdefault("items", {})
    if not isinstance(data["items"], dict):
        raise ValueError("segments.json 的 items 必须是 object")
    return data


def save_segments(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data.setdefault("version", 2)
    data.setdefault("items", {})
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_audio_markers(path: Path, audio: AudioInfo, markers: SegmentMarkers, notes: str = "") -> None:
    data = load_segments(path)
    data.setdefault("items", {})
    data["items"][audio.filename] = {
        "sample_rate": audio.samplerate,
        "sample_length": audio.sample_length,
        "duration_sec": audio.duration_sec,
        "markers": markers_to_json(markers),
        "notes": notes,
    }
    save_segments(path, data)


def get_audio_markers_from_segments(
    segments_data: dict,
    audio: AudioInfo,
    fallback_names: list[str] | None = None,
) -> SegmentMarkers:
    items = segments_data.get("items", {}) if segments_data else {}
    candidate_names = [audio.filename]
    if fallback_names:
        candidate_names.extend([x for x in fallback_names if x not in candidate_names])

    for name in candidate_names:
        item = items.get(name)
        if isinstance(item, dict) and isinstance(item.get("markers"), dict):
            return markers_from_json(item["markers"], audio)

    return default_markers_for_audio(audio)


def markers_for_prepare_record(
    segments_data: dict,
    record: AudioPrepareRecord,
) -> SegmentMarkers | None:
    if record.output_info is None:
        return None

    fallback = []
    if record.source_path is not None:
        fallback.append(record.source_path.name)
    if record.output_path is not None:
        fallback.append(record.output_path.name)

    return get_audio_markers_from_segments(segments_data, record.output_info, fallback)
