from __future__ import annotations

import shutil
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from .bank_tools import is_track_bank_name
from .core import PrepareOptions, prepare_dry_run
from .metadata_tools import load_track_metadata
from .models import AudioInfo, SegmentMarkers, TrackPatch
from .order_tools import FMOD_EXTRACT_TEMPLATE_DIR_NAME, FMOD_REBUILD_WORKSPACE_DIR_NAME, FMOD_READY_WAV_DIR_NAME, TRACK_ORDER_FILE_NAME, create_fmod_rebuild_workspace, ensure_track_order_file, read_track_order, validate_track_order
from .segment_tools import load_segments, markers_from_json
from .xml_tools import patch_station_samples, write_xml
from .xml_tools import find_station, parse_xml, station_info_from_node
from .wav_tools import read_wav_info


@dataclass(frozen=True)
class ProjectPrepareResult:
    station_name: str
    output_dir: Path
    backup_dir: Path
    work_dir: Path
    output_xml: Path
    output_assets_txts: list[Path]
    internal_patched_xml: Path
    adopted_files: list[str]
    used_count: int
    normalized_count: int
    original_count: int
    discarded_count: int
    discarded_files: list[str]
    backup_snapshot_dir: Path | None


@dataclass(frozen=True)
class XmlRegenerateResult:
    station_name: str
    output_xml: Path
    output_dir: Path
    changed_slots: int



def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def project_output_dir() -> Path:
    return project_root() / "output"


def project_backup_dir() -> Path:
    return project_root() / "backup"


def project_work_dir() -> Path:
    return project_root() / "work"


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_project_dirs() -> tuple[Path, Path, Path]:
    out = project_output_dir()
    bak = project_backup_dir()
    work = project_work_dir()
    out.mkdir(parents=True, exist_ok=True)
    bak.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    return out, bak, work


def cleanup_output_dir() -> None:
    out, _, _ = ensure_project_dirs()
    # 运行中允许 output 暂存用户可编辑文件；最终由 finalize_public_output 收口。
    allowed_suffixes = {".xml", ".csv"}
    keep_dirs = {FMOD_READY_WAV_DIR_NAME}
    for p in out.iterdir():
        if p.is_dir():
            if p.name not in keep_dirs:
                shutil.rmtree(p, ignore_errors=True)
        elif p.suffix.lower() not in allowed_suffixes:
            p.unlink(missing_ok=True)


def finalize_public_output(output_xml: Path, metadata_path: Path) -> None:
    """最终收口 output，让用户只看到三个必要产物。

    保留：
    - output/RadioInfo_CN.xml
    - output/track_metadata.csv
    - output/fmod_ready_wav/

    其他映射、报告、说明、中间文件统一删除或放在 work/。
    """
    out, _, _ = ensure_project_dirs()
    keep_files = {Path(output_xml).resolve(), Path(metadata_path).resolve()}
    keep_dirs = {FMOD_READY_WAV_DIR_NAME}

    for p in out.iterdir():
        if p.is_dir():
            if p.name not in keep_dirs:
                shutil.rmtree(p, ignore_errors=True)
        else:
            if p.resolve() not in keep_files:
                p.unlink(missing_ok=True)


def backup_original_xml(xml_path: Path) -> Path:
    _, bak, _ = ensure_project_dirs()
    snap = bak / _timestamp()
    snap.mkdir(parents=True, exist_ok=True)
    xml_path = Path(xml_path)
    if xml_path.exists():
        shutil.copy2(xml_path, snap / xml_path.name)
    return snap


def selected_track_bank_names(xml_path: Path, station_name: str) -> list[str]:
    tree = parse_xml(xml_path)
    station = station_info_from_node(find_station(tree, station_name))
    banks = station.banks
    track_banks = [b for b in banks if is_track_bank_name(b)]
    cu1 = [b for b in track_banks if "cu1" in b.lower()]
    if cu1:
        return [cu1[0]]
    if track_banks:
        return [track_banks[0]]
    return banks[:1]


def write_assets_txts(xml_path: Path, station_name: str, adopted_files: list[str]) -> list[Path]:
    out, _, _ = ensure_project_dirs()
    bank_names = selected_track_bank_names(xml_path, station_name)
    txts: list[Path] = []
    for bank in bank_names:
        txt_path = out / f"{bank}.assets.txt"
        txt_path.write_text("\n".join(adopted_files) + ("\n" if adopted_files else ""), encoding="utf-8")
        txts.append(txt_path)
    return txts


def write_manual_steps(result: ProjectPrepareResult, music_dir: Path) -> Path:
    path = result.work_dir / "manual_fmod_bank_tools_steps.txt"
    lines = [
        "FH6 Radio Tool v2.0 手动 bank 重构提示",
        "=" * 48,
        "",
        "本工具不再自动调用或适配 Fmod Bank Tools。",
        "请用户自行使用 Fmod Bank Tools 对 bank 进行重构。",
        "",
        "本工具已生成：",
        f"  XML: {result.output_xml}",
    ]
    for p in result.output_assets_txts:
        lines.append(f"  TXT: {p}")
    lines += [
        "",
        "推荐手动流程：",
        "1. 打开 Fmod Bank Tools。",
        "2. 按教程设置 bank / wav / build / cache 目录。",
        "3. 将原游戏 bank 放入 Fmod Bank Tools 的 bank 目录。",
        "4. 使用 Fmod Bank Tools 提取/准备对应 bank。",
        "5. 使用 output 中的 *.assets.txt 作为重构清单。",
        "6. 确保 txt 中列出的 wav 文件与音乐文件夹中的实际文件名一致。",
        f"7. 当前音乐文件夹：{music_dir}",
        "8. 在 Fmod Bank Tools 中完成重构后，再按教程替换游戏 bank。",
        "",
        "注意：",
        "- assets.txt 中只写文件名，不写路径。",
        "- 如果某首歌被规范化，txt 中会使用 *_fh6_norm.wav。",
        "- output 目录不会生成 bank 文件。",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _audio_info_from_dict(data: dict) -> AudioInfo:
    return AudioInfo(
        path=Path(data.get("path") or ""),
        filename=str(data.get("filename") or Path(data.get("path") or "").name),
        samplerate=int(data.get("samplerate") or 48000),
        channels=int(data.get("channels") or 2),
        bits_per_sample=int(data.get("bits_per_sample") or 16),
        frames=int(data.get("frames") or data.get("sample_length") or 0),
        duration_sec=float(data.get("duration_sec") or 0.0),
    )


def _audio_maps_from_prepare_result(result: dict) -> dict[str, AudioInfo]:
    audio_by_filename: dict[str, AudioInfo] = {}
    records = result.get("manifest", {}).get("audio_prepare_records", []) or []
    for rec in records:
        info = rec.get("output_info") or {}
        if not info:
            continue
        audio = _audio_info_from_dict(info)
        audio_by_filename[audio.filename] = audio

    # fallback: patches 里也有 audio
    for patch in result.get("patches", []) or []:
        info = patch.get("audio") or {}
        if info:
            audio = _audio_info_from_dict(info)
            audio_by_filename.setdefault(audio.filename, audio)

    return audio_by_filename


def _marker_maps_from_prepare_result(result: dict) -> dict[str, SegmentMarkers]:
    markers: dict[str, SegmentMarkers] = {}
    usage = result.get("segment_usage", {}) or {}
    for filename, data in usage.items():
        if isinstance(data, dict):
            try:
                markers[filename] = markers_from_json(data)
            except Exception:
                pass
    return markers


def _patch_xml_by_track_order(
    xml_path: Path,
    station_name: str,
    output_xml: Path,
    rows,
    audio_by_filename: dict[str, AudioInfo],
    markers_by_filename: dict[str, SegmentMarkers],
) -> None:
    tree = parse_xml(xml_path)

    patches: list[TrackPatch] = []
    for row in rows:
        if not row.audio_filename:
            continue
        audio = audio_by_filename[row.audio_filename]
        metadata_artist = row.artist or "User"
        display_name = row.display_name or Path(row.audio_filename).stem
        markers = markers_by_filename.get(row.audio_filename)
        if markers is None:
            markers = SegmentMarkers({
                "TrackStart": 0,
                "End": max(0, audio.sample_length - 1),
            })

        patches.append(
            TrackPatch(
                slot_index=row.slot_index,
                audio=audio,
                display_name=display_name,
                artist=metadata_artist,
                sound_name="",  # patch_station_samples 会保留原 SoundName
                markers=markers,
            )
        )

    patched = patch_station_samples(tree, station_name, patches)
    write_xml(patched, output_xml)


def _audio_maps_from_ready_wav_or_music(
    rows,
    music_dir: Path,
    ready_wav_dir: Path,
) -> dict[str, AudioInfo]:
    """Build AudioInfo map without running normalization or loudness matching.

    Used by the lightweight XML-only regeneration path.
    Preference:
    1. output/fmod_ready_wav/<original_wav_relpath> because this is the exact
       audio that will be rebuilt into the bank after full Generate.
    2. music_dir/<audio_filename> as fallback.
    """
    result: dict[str, AudioInfo] = {}
    music_dir = Path(music_dir)
    ready_wav_dir = Path(ready_wav_dir)

    for row in rows:
        if not row.audio_filename:
            continue

        candidates: list[Path] = []
        if row.original_wav_relpath:
            candidates.append(ready_wav_dir / row.original_wav_relpath)
        candidates.append(music_dir / row.audio_filename)

        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                try:
                    info = read_wav_info(candidate)
                    # Key by original user-facing audio filename because
                    # track_order.csv rows refer to audio_filename.
                    result[row.audio_filename] = AudioInfo(
                        path=info.path,
                        filename=row.audio_filename,
                        samplerate=info.samplerate,
                        channels=info.channels,
                        bits_per_sample=info.bits_per_sample,
                        frames=info.frames,
                        duration_sec=info.duration_sec,
                    )
                    break
                except Exception:
                    continue

    return result


def _marker_maps_from_segments_file(path: Path, audio_by_filename: dict[str, AudioInfo]) -> dict[str, SegmentMarkers]:
    markers: dict[str, SegmentMarkers] = {}
    if not Path(path).exists():
        return markers

    data = load_segments(path)
    items = data.get("items", {}) if isinstance(data, dict) else {}
    for filename, item in items.items():
        if not isinstance(item, dict):
            continue
        raw_markers = item.get("markers")
        if not isinstance(raw_markers, dict):
            continue
        try:
            markers[filename] = markers_from_json(raw_markers, audio_by_filename.get(filename))
        except Exception:
            continue
    return markers


def regenerate_xml_only(xml_path: Path, music_dir: Path, station_name: str) -> XmlRegenerateResult:
    """Lightweight path for marker/metadata-only edits.

    This does NOT:
    - normalize audio;
    - rebuild fmod_ready_wav;
    - run loudness matching;
    - rebuild track mapping.

    It only reuses existing work/track_order.csv + output/track_metadata.csv +
    work/segments.json to rewrite output/RadioInfo_CN.xml.
    """
    out, _bak, work = ensure_project_dirs()
    output_xml = out / Path(xml_path).name
    track_order_path = work / TRACK_ORDER_FILE_NAME
    metadata_path = out / "track_metadata.csv"
    segments_path = work / "segments.json"
    ready_wav_dir = out / FMOD_READY_WAV_DIR_NAME

    if not track_order_path.exists():
        raise FileNotFoundError(
            "Missing work/track_order.csv. Please run ③ Generate once before using XML-only regeneration."
        )

    rows = read_track_order(track_order_path)
    if not rows:
        raise ValueError("work/track_order.csv is empty. Please run ③ Generate again.")

    audio_by_filename = _audio_maps_from_ready_wav_or_music(rows, Path(music_dir), ready_wav_dir)
    missing_audio = [
        row.audio_filename for row in rows
        if row.audio_filename and row.audio_filename not in audio_by_filename
    ]
    if missing_audio:
        preview = "\n".join(f"- {x}" for x in missing_audio[:10])
        raise FileNotFoundError(
            "Cannot rewrite XML only because some prepared audio files were not found.\n"
            "Please run full ③ Generate again.\n"
            + preview
        )

    metadata = load_track_metadata(metadata_path)
    refreshed_rows = []
    for row in rows:
        meta = metadata.get(row.audio_filename)
        if meta:
            refreshed_rows.append(replace(row, display_name=meta.display_name, artist=meta.artist))
        else:
            refreshed_rows.append(row)

    markers_by_filename = _marker_maps_from_segments_file(segments_path, audio_by_filename)

    _patch_xml_by_track_order(
        Path(xml_path),
        station_name,
        output_xml,
        refreshed_rows,
        audio_by_filename,
        markers_by_filename,
    )

    return XmlRegenerateResult(
        station_name=station_name,
        output_xml=output_xml,
        output_dir=out,
        changed_slots=len([r for r in refreshed_rows if r.audio_filename]),
    )


def prepare_project_outputs(xml_path: Path, music_dir: Path, station_name: str, progress_callback=None) -> ProjectPrepareResult:
    out, bak, work = ensure_project_dirs()
    prepare_work = work / "prepare"
    if prepare_work.exists():
        shutil.rmtree(prepare_work, ignore_errors=True)
    prepare_work.mkdir(parents=True, exist_ok=True)

    if progress_callback:
        progress_callback(3, "Backing up XML...")
    backup_snapshot = backup_original_xml(xml_path)

    # 用户可见文件尽量只放 output；中间映射与 Extract 模板放 work。
    metadata_path = out / "track_metadata.csv"
    segments_path = work / "segments.json"
    track_order_path = work / TRACK_ORDER_FILE_NAME
    extract_template_dir = work / FMOD_EXTRACT_TEMPLATE_DIR_NAME

    # 第一次 prepare 负责：音频校验/规范化、生成 adopted_files、生成 metadata/segments 中间信息。
    if progress_callback:
        progress_callback(10, "Checking and normalizing audio...")
    result = prepare_dry_run(
        PrepareOptions(
            xml_path=Path(xml_path),
            station_name=station_name,
            music_dir=Path(music_dir),
            out_dir=prepare_work,
            auto_normalize=True,
            ffmpeg_path=None,
            segments_path=segments_path,
            metadata_path=metadata_path,
        )
    )

    if progress_callback:
        progress_callback(97, "Cleaning final output folder...")
    cleanup_output_dir()

    adopted_files = list(result.get("adopted_files", []))

    if progress_callback:
        progress_callback(35, "Building XML-to-FMOD track mapping...")
    ensure_track_order_file(
        track_order_path,
        Path(xml_path),
        station_name,
        adopted_files,
        Path(metadata_path),
        extract_template_dir,
    )

    rows = read_track_order(track_order_path)
    audio_by_filename = _audio_maps_from_prepare_result(result)
    order_errors = validate_track_order(rows, audio_by_filename)
    if order_errors:
        raise ValueError(
            "track_order.csv 存在无法应用的问题：\n" + "\n".join(order_errors)
        )

    if progress_callback:
        progress_callback(48, "Writing patched XML...")
    output_xml = out / Path(xml_path).name
    markers_by_filename = _marker_maps_from_prepare_result(result)
    _patch_xml_by_track_order(
        Path(xml_path),
        station_name,
        output_xml,
        rows,
        audio_by_filename,
        markers_by_filename,
    )

    # assets.txt 也严格按照 track_order.csv 输出，保证 txt 与 XML 使用同一套顺序。
    # assets.txt 必须按 bank_index 输出；XML 则按 slot_index 写入。
    ordered_adopted_files = [
        row.audio_filename
        for row in sorted(rows, key=lambda r: r.bank_index)
        if row.audio_filename
    ]
    # 旧版会输出 assets.txt；v3.4 正式版不再公开该文件，避免误用。
    assets_txts = []

    # v2.9：真正给 Fmod Bank Tools 使用的是这个工作区。
    # 它保留原 Extract txt 和 wav 文件名，只替换音频内容，避免显示名/实际音频错位。
    if extract_template_dir.exists():
        if progress_callback:
            progress_callback(58, "Creating fmod_ready_wav...")
        create_fmod_rebuild_workspace(
            out,
            extract_template_dir,
            rows,
            audio_by_filename,
            progress_callback=progress_callback,
        )
        # 用户最终只需要 fmod_ready_wav；辅助 bank/build/cache 目录移动到 work。
        aux_ws = out / FMOD_REBUILD_WORKSPACE_DIR_NAME
        if aux_ws.exists():
            dst_ws = work / FMOD_REBUILD_WORKSPACE_DIR_NAME
            if dst_ws.exists():
                shutil.rmtree(dst_ws, ignore_errors=True)
            shutil.move(str(aux_ws), str(dst_ws))

    cleanup_output_dir()

    pr = ProjectPrepareResult(
        station_name=station_name,
        output_dir=out,
        backup_dir=bak,
        work_dir=work,
        output_xml=output_xml,
        output_assets_txts=assets_txts,
        internal_patched_xml=result["patched_xml_path"],
        adopted_files=ordered_adopted_files,
        used_count=len(ordered_adopted_files),
        normalized_count=int(result.get("normalized_count", 0)),
        original_count=int(result.get("original_count", 0)),
        discarded_count=int(result.get("discarded_count", 0)),
        discarded_files=list(result.get("discarded_files", [])),
        backup_snapshot_dir=backup_snapshot,
    )
    write_manual_steps(pr, Path(music_dir))

    note = work / "internal_mapping_note.txt"
    note.write_text(
        "内部文件说明：track_order.csv / fmod_extract_template / volume_match_report 等均为调试或中间文件。\n"
        "正式给用户看的 output 只保留 XML、track_metadata.csv、fmod_ready_wav。\n",
        encoding="utf-8",
    )

    if progress_callback:
        progress_callback(98, "Finalizing public output...")
    finalize_public_output(output_xml, metadata_path)

    if progress_callback:
        progress_callback(100, "Generation complete.")
    return pr
