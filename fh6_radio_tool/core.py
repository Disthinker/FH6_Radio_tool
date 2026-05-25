from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .ffmpeg_tools import prepare_one_audio
from .marker_normalization import normalize_track_markers_for_prepared_audio
from .segment_tools import SEGMENTS_FILE_NAME, load_segments, markers_for_prepare_record, markers_to_json
from .metadata_tools import METADATA_FILE_NAME, metadata_for_output_filenames
from .txt_tools import write_bank_wav_list
from .wav_tools import list_audio_candidates
from .xml_tools import (
    build_track_patches,
    find_station,
    parse_xml,
    patch_station_samples,
    station_info_from_node,
    write_xml,
)


@dataclass(frozen=True)
class PrepareOptions:
    xml_path: Path
    station_name: str
    music_dir: Path
    out_dir: Path
    artist: str = "User"
    sound_prefix: str = "HZ6_USER"
    expected_samplerate: int = 48000
    expected_channels: int = 2
    expected_bits: int = 16
    auto_normalize: bool = True
    ffmpeg_path: str | None = None
    bank_txt_name: str = "bank_wav_list.txt"
    segments_path: Path | None = None
    metadata_path: Path | None = None


class InvalidAudioError(RuntimeError):
    def __init__(self, invalid_files: dict[str, list[str]]):
        self.invalid_files = invalid_files
        message = "存在无法规范化或不符合要求的音频文件，已中止 dry-run 生成。"
        super().__init__(message)


def _raw_markers_for_prepare_record(segments_data: dict, record) -> dict | None:
    if record.output_info is None:
        return None
    items = segments_data.get("items", {}) if isinstance(segments_data, dict) else {}
    names = [record.output_info.filename]
    if record.source_path is not None:
        names.append(record.source_path.name)
    if record.output_path is not None:
        names.append(record.output_path.name)
    for name in names:
        item = items.get(name)
        if isinstance(item, dict) and isinstance(item.get("markers"), dict):
            return dict(item["markers"])
    return None


def _path_json_default(obj):
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def prepare_dry_run(options: PrepareOptions) -> dict:
    """生成 dry-run 输出，不写回游戏目录。

    v0.5 新增：
    - 若 music_dir/segments.json 存在，则 XML patch 优先使用其中的 Marker；
    - 没有 Marker 的音频仍使用默认 TrackStart=0, End=sample_length-1。
    """
    tree = parse_xml(options.xml_path)
    station_node = find_station(tree, options.station_name)
    station = station_info_from_node(station_node)

    candidates = list_audio_candidates(options.music_dir)
    selected_sources = candidates[: station.track_slot_count]
    discarded_sources = candidates[station.track_slot_count :]

    options.out_dir.mkdir(parents=True, exist_ok=True)

    prepare_records = []
    for source in selected_sources:
        record = prepare_one_audio(
            source=source,
            auto_normalize=options.auto_normalize,
            ffmpeg_path=options.ffmpeg_path,
            expected_samplerate=options.expected_samplerate,
            expected_channels=options.expected_channels,
            expected_bits=options.expected_bits,
        )
        prepare_records.append(record)

    invalid_files = {str(r.source_path): r.errors for r in prepare_records if r.status == "failed"}
    if invalid_files:
        raise InvalidAudioError(invalid_files)

    output_infos = [
        r.output_info
        for r in prepare_records
        if r.output_info is not None and r.output_path is not None
    ]

    segments_path = options.segments_path or (options.music_dir / SEGMENTS_FILE_NAME)
    segments_data = load_segments(segments_path) if segments_path.exists() else {"version": 1, "items": {}}

    markers_by_filename = {}
    segment_usage = {}
    for record in prepare_records:
        if record.output_info is None:
            continue
        raw_markers = _raw_markers_for_prepare_record(segments_data, record)
        markers = (
            normalize_track_markers_for_prepared_audio(
                raw_markers,
                record.source_info,
                record.output_info,
                marker_unit="samples",
                label=record.output_info.filename,
            ).markers
            if raw_markers is not None
            else markers_for_prepare_record(segments_data, record)
        )
        if markers is None:
            continue
        markers_by_filename[record.output_info.filename] = markers
        segment_usage[record.output_info.filename] = markers_to_json(markers)

    output_filenames = [info.filename for info in output_infos]
    metadata_path = options.metadata_path or (options.music_dir / METADATA_FILE_NAME)
    metadata_by_filename = metadata_for_output_filenames(metadata_path, output_filenames)

    metadata_usage = {
        filename: {
            "display_name": item.display_name,
            "artist": item.artist,
        }
        for filename, item in metadata_by_filename.items()
        if filename in set(output_filenames)
    }

    patches = build_track_patches(
        station=station,
        audios=output_infos,
        artist=options.artist,
        sound_prefix=options.sound_prefix,
        markers_by_filename=markers_by_filename,
        metadata_by_filename=metadata_by_filename,
    )

    bank_txt_path = options.out_dir / options.bank_txt_name
    patched_xml_path = options.out_dir / f"{options.xml_path.stem}.patched.xml"
    manifest_path = options.out_dir / "patch_manifest.json"
    audio_report_path = options.out_dir / "audio_report.json"

    write_bank_wav_list(patches, bank_txt_path)
    patched_tree = patch_station_samples(tree, options.station_name, patches)
    write_xml(patched_tree, patched_xml_path)

    audio_report = {
        "selected": [asdict(r) for r in prepare_records],
        "discarded": [str(p) for p in discarded_sources],
        "segments_path": str(segments_path),
        "metadata_path": str(metadata_path),
        "segment_usage": segment_usage,
        "metadata_usage": metadata_usage,
    }
    audio_report_path.write_text(
        json.dumps(audio_report, ensure_ascii=False, indent=2, default=_path_json_default),
        encoding="utf-8",
    )

    manifest = {
        "mode": "dry-run",
        "xml": str(options.xml_path),
        "station": asdict(station),
        "music_dir": str(options.music_dir),
        "out_dir": str(options.out_dir),
        "bank_txt_path": str(bank_txt_path),
        "segments_path": str(segments_path),
        "metadata_path": str(metadata_path),
        "auto_normalize": options.auto_normalize,
        "ffmpeg_path": options.ffmpeg_path,
        "expected_audio": {
            "samplerate": options.expected_samplerate,
            "channels": options.expected_channels,
            "bits_per_sample": options.expected_bits,
        },
        "candidate_count": len(candidates),
        "used_count": len(output_infos),
        "discarded_count": len(discarded_sources),
        "discarded_files": [p.name for p in discarded_sources],
        "outputs": {
            "bank_wav_list": str(bank_txt_path),
            "patched_xml": str(patched_xml_path),
            "audio_report": str(audio_report_path),
        },
        "audio_prepare_records": [asdict(r) for r in prepare_records],
        "segment_usage": segment_usage,
        "metadata_usage": metadata_usage,
        "patches": [asdict(p) for p in patches],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=_path_json_default),
        encoding="utf-8",
    )

    return {
        "station": station,
        "candidate_count": len(candidates),
        "used_count": len(output_infos),
        "discarded_count": len(discarded_sources),
        "discarded_files": [p.name for p in discarded_sources],
        "normalized_count": len([r for r in prepare_records if r.status == "normalized"]),
        "original_count": len([r for r in prepare_records if r.status == "ok"]),
        "bank_txt_path": bank_txt_path,
        "patched_xml_path": patched_xml_path,
        "manifest_path": manifest_path,
        "audio_report_path": audio_report_path,
        "segments_path": segments_path,
        "metadata_path": metadata_path,
        "segment_usage": segment_usage,
        "metadata_usage": metadata_usage,
        "adopted_files": [r.output_info.filename for r in prepare_records if r.output_info],
        "manifest": manifest,
    }
