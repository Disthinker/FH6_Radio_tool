from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AudioInfo:
    path: Path
    filename: str
    samplerate: int
    channels: int
    bits_per_sample: int
    frames: int
    duration_sec: float

    @property
    def sample_length(self) -> int:
        """XML 中 SampleLength 当前按帧数/采样点数处理。"""
        return self.frames


@dataclass(frozen=True)
class AudioValidationResult:
    info: AudioInfo | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.info is not None


@dataclass(frozen=True)
class AudioPrepareRecord:
    """记录单个输入音频到最终采用音频的处理过程。"""
    source_path: Path
    status: str
    source_info: AudioInfo | None = None
    output_info: AudioInfo | None = None
    output_path: Path | None = None
    action: str = "unknown"
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SegmentMarkers:
    """Track Marker 设置。

    positions 使用 XML Marker 名称作为 key，例如：
    TrackStart / TrackDrop / TrackLoopStart / TrackLoopEnd / PostDrop /
    PostRaceLoopStart / PostRaceLoopEnd / DJSegment / StingerStart / DJStart / End

    值为采样点。-1 或缺失表示不写入/保留模板原值。
    """
    positions: dict[str, int] = field(default_factory=dict)

    def get(self, name: str, default: int | None = None) -> int | None:
        return self.positions.get(name, default)


@dataclass(frozen=True)
class TrackPatch:
    slot_index: int
    audio: AudioInfo
    display_name: str
    artist: str
    sound_name: str
    markers: SegmentMarkers


@dataclass(frozen=True)
class StationInfo:
    name: str
    number: str | None
    banks: list[str]
    track_slot_count: int
    sample_rates: list[int]


@dataclass(frozen=True)
class PrepareResult:
    station: StationInfo
    used_tracks: list[TrackPatch]
    discarded_files: list[str]
    invalid_files: dict[str, list[str]]
    bank_txt_path: Path
    patched_xml_path: Path
    manifest_path: Path
