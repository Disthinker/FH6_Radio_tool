from __future__ import annotations

import csv
import re
import shutil
import wave
from dataclasses import dataclass
from pathlib import Path

from .metadata_tools import TrackMetadata, guess_display_artist_from_filename, load_track_metadata
from .models import AudioInfo
from .xml_tools import find_station, get_track_samples, parse_xml
from .volume_tools import match_loudness_to_reference


TRACK_ORDER_FILE_NAME = "track_order.csv"
FMOD_EXTRACT_TEMPLATE_DIR_NAME = "fmod_extract_template"
FMOD_REBUILD_WORKSPACE_DIR_NAME = "fmod_rebuild_workspace"
FMOD_READY_WAV_DIR_NAME = "fmod_ready_wav"

# Kept for backward compatibility with older UI/docs. v2.9 no longer uses a single txt.
FMOD_EXTRACT_LIST_NAME = "fmod_extracted_assets.txt"


@dataclass(frozen=True)
class ExtractRecord:
    bank_index: int
    txt_relpath: str
    txt_stem: str
    subsound_index: int
    extracted_name: str
    extracted_stem: str
    original_relpath: str
    match_key: str
    frames: int = 0
    samplerate: int = 0
    duration_sec: float = 0.0


@dataclass(frozen=True)
class TrackOrderRow:
    slot_index: int
    bank_index: int
    fsb_txt: str
    subsound_index: int
    sound_name: str
    original_display_name: str
    original_artist: str
    extracted_name: str
    original_wav_relpath: str
    xml_sample_length: int
    xml_sample_rate: int
    extract_frames: int
    extract_sample_rate: int
    length_delta: int
    audio_filename: str
    display_name: str
    artist: str
    match_method: str = ""
    confidence: str = ""
    notes: str = ""


FIELDNAMES = [
    "slot_index",
    "bank_index",
    "fsb_txt",
    "subsound_index",
    "sound_name",
    "original_display_name",
    "original_artist",
    "extracted_name",
    "original_wav_relpath",
    "xml_sample_length",
    "xml_sample_rate",
    "extract_frames",
    "extract_sample_rate",
    "length_delta",
    "audio_filename",
    "display_name",
    "artist",
    "match_method",
    "confidence",
    "notes",
]


def _clean(text: object) -> str:
    return str(text or "").strip().replace("\ufeff", "")


def _to_int(text: object, default: int = -1) -> int:
    try:
        return int(_clean(text))
    except Exception:
        return default


def natural_key(value: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", str(value))]


def normalize_key(text: str) -> str:
    text = Path(str(text or "")).stem
    text = text.lower()
    text = re.sub(r"\bhz[0-9]+\b", "", text)
    text = re.sub(r"\br[0-9]+\b", "", text)
    text = re.sub(r"\btracks?\b", "", text)
    text = re.sub(r"\bcu[0-9]+\b", "", text)
    text = re.sub(r"\bassets?\b", "", text)
    text = re.sub(r"\[[0-9]+\]", "", text)
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)
    return text


def compact_artist_title(artist: str, title: str) -> str:
    return normalize_key(f"{artist}_{title}")


def import_fmod_extract_folder(src: Path, target_dir: Path) -> Path:
    """导入 Fmod Bank Tools Extract 后的 wav 输出目录。

    v2.9 不再导入单个 txt，而是导入完整 Extract 目录。
    这样可以保留 Fmod Bank Tools 的真实 txt 命名、子目录和 subsound 顺序。
    """
    src = Path(src)
    if not src.exists() or not src.is_dir():
        raise ValueError(f"不是有效的 Fmod Extract 目录：{src}")

    dst = Path(target_dir) / FMOD_EXTRACT_TEMPLATE_DIR_NAME
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)

    records = parse_extract_template(dst)
    if not records:
        raise ValueError(
            "导入的目录中没有识别到 Fmod Bank Tools 的提取 txt。"
            "请选择 Fmod Bank Tools 的 Wav Output Directory，里面应包含 *.txt 和对应 wav 子目录。"
        )
    return dst


def import_fmod_extract_list(src: Path, target_dir: Path) -> Path:
    """兼容旧按钮/旧调用：单 txt 导入已不推荐。"""
    dst = Path(target_dir) / FMOD_EXTRACT_LIST_NAME
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def _read_txt_lines(path: Path) -> list[str]:
    try:
        raw = path.read_text(encoding="utf-8-sig", errors="ignore")
    except Exception:
        return []
    lines = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        lines.append(line)
    return lines


def _looks_like_fmod_txt(path: Path, lines: list[str]) -> bool:
    if not lines:
        return False
    name = path.name.lower()
    if "readme" in name or "manual" in name:
        return False
    # Fmod Bank Tools 的 txt 行通常是 wav 文件名。
    wav_like = sum(1 for line in lines if line.lower().endswith(".wav"))
    return wav_like > 0 or len(lines) >= 2


def _find_original_wav_relpath(template_dir: Path, txt_path: Path, line: str) -> str:
    """按 Fmod Bank Tools RebuildWorker 的逻辑推断原 wav 位置。

    RebuildWorker 通常按：wavDir / txt.completeBaseName() / line 查找。
    如果原提取目录结构不同，则尽量定位已有同名 wav。
    """
    line_path = Path(line)
    candidates = [
        template_dir / txt_path.stem / line_path.name,
        txt_path.parent / line_path.name,
        template_dir / line_path.name,
    ]

    for cand in candidates:
        if cand.exists():
            return cand.relative_to(template_dir).as_posix()

    matches = list(template_dir.rglob(line_path.name))
    if matches:
        matches = sorted(matches, key=lambda p: natural_key(p.as_posix()))
        return matches[0].relative_to(template_dir).as_posix()

    # 如果原 wav 没找到，仍按 RebuildWorker 规则创建目标路径。
    return (Path(txt_path.stem) / line_path.name).as_posix()



def _read_wav_basic(path: Path) -> tuple[int, int, float]:
    """Return frames, samplerate, duration_sec for a WAV file.

    If reading fails, return zero values. Fmod Extract wavs should normally
    be readable by Python's wave module.
    """
    try:
        with wave.open(str(path), "rb") as wf:
            frames = int(wf.getnframes())
            samplerate = int(wf.getframerate())
            duration = frames / samplerate if samplerate else 0.0
            return frames, samplerate, duration
    except Exception:
        return 0, 0, 0.0


def parse_extract_template(template_dir: Path) -> list[ExtractRecord]:
    template_dir = Path(template_dir)
    if not template_dir.exists():
        return []

    txts = []
    for txt in template_dir.rglob("*.txt"):
        lines = _read_txt_lines(txt)
        if _looks_like_fmod_txt(txt, lines):
            txts.append((txt, lines))

    txts.sort(key=lambda item: natural_key(item[0].relative_to(template_dir).as_posix()))

    records: list[ExtractRecord] = []
    bank_index = 0
    for txt, lines in txts:
        txt_relpath = txt.relative_to(template_dir).as_posix()
        for subsound_index, line in enumerate(lines):
            extracted_name = Path(line).name
            extracted_stem = Path(extracted_name).stem
            original_relpath = _find_original_wav_relpath(template_dir, txt, extracted_name)
            wav_path = template_dir / original_relpath
            frames, samplerate, duration_sec = _read_wav_basic(wav_path)
            records.append(
                ExtractRecord(
                    bank_index=bank_index,
                    txt_relpath=txt_relpath,
                    txt_stem=txt.stem,
                    subsound_index=subsound_index,
                    extracted_name=extracted_name,
                    extracted_stem=extracted_stem,
                    original_relpath=original_relpath,
                    match_key=normalize_key(extracted_stem),
                    frames=frames,
                    samplerate=samplerate,
                    duration_sec=duration_sec,
                )
            )
            bank_index += 1

    return records


def read_track_order(path: Path) -> list[TrackOrderRow]:
    if not path.exists():
        return []

    rows: list[TrackOrderRow] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for item in reader:
            slot = _to_int(item.get("slot_index"), -1)
            if slot < 0:
                continue
            bank_index = _to_int(item.get("bank_index"), slot)
            rows.append(
                TrackOrderRow(
                    slot_index=slot,
                    bank_index=bank_index,
                    fsb_txt=_clean(item.get("fsb_txt")),
                    subsound_index=_to_int(item.get("subsound_index"), bank_index),
                    sound_name=_clean(item.get("sound_name")),
                    original_display_name=_clean(item.get("original_display_name")),
                    original_artist=_clean(item.get("original_artist")),
                    extracted_name=_clean(item.get("extracted_name")),
                    original_wav_relpath=_clean(item.get("original_wav_relpath")),
                    xml_sample_length=_to_int(item.get("xml_sample_length"), 0),
                    xml_sample_rate=_to_int(item.get("xml_sample_rate"), 0),
                    extract_frames=_to_int(item.get("extract_frames"), 0),
                    extract_sample_rate=_to_int(item.get("extract_sample_rate"), 0),
                    length_delta=_to_int(item.get("length_delta"), 0),
                    audio_filename=_clean(item.get("audio_filename")),
                    display_name=_clean(item.get("display_name")),
                    artist=_clean(item.get("artist")),
                    match_method=_clean(item.get("match_method")),
                    confidence=_clean(item.get("confidence")),
                    notes=_clean(item.get("notes")),
                )
            )
    rows.sort(key=lambda x: x.slot_index)
    return rows


def write_track_order(path: Path, rows: list[TrackOrderRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda x: x.slot_index)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "slot_index": row.slot_index,
                "bank_index": row.bank_index,
                "fsb_txt": row.fsb_txt,
                "subsound_index": row.subsound_index,
                "sound_name": row.sound_name,
                "original_display_name": row.original_display_name,
                "original_artist": row.original_artist,
                "extracted_name": row.extracted_name,
                "original_wav_relpath": row.original_wav_relpath,
                "xml_sample_length": row.xml_sample_length,
                "xml_sample_rate": row.xml_sample_rate,
                "extract_frames": row.extract_frames,
                "extract_sample_rate": row.extract_sample_rate,
                "length_delta": row.length_delta,
                "audio_filename": row.audio_filename,
                "display_name": row.display_name,
                "artist": row.artist,
                "match_method": row.match_method,
                "confidence": row.confidence,
                "notes": row.notes,
            })


def station_sample_rows(xml_path: Path, station_name: str) -> list[dict]:
    tree = parse_xml(xml_path)
    station = find_station(tree, station_name)
    samples = get_track_samples(station)
    rows = []
    for idx, sample in enumerate(samples):
        rows.append({
            "slot_index": idx,
            "sound_name": sample.get("SoundName", ""),
            "original_display_name": sample.get("DisplayName", ""),
            "original_artist": sample.get("Artist", ""),
            "sample_length": _to_int(sample.get("SampleLength"), 0),
            "sample_rate": _to_int(sample.get("SampleRate"), 0),
        })
    return rows


def _build_record_match_lists(records: list[ExtractRecord]):
    return [
        {
            "record": record,
            "key": record.match_key,
            "name": record.extracted_name,
        }
        for record in records
    ]


def _length_tolerance(frames: int) -> int:
    """Tolerance for XML SampleLength vs extracted wav frames.

    Most original assets should match exactly. Small tolerances handle
    encoder/extractor padding or off-by-one metadata differences.
    """
    if frames <= 0:
        return 0
    return max(8, int(frames * 0.0005))  # 0.05%, min 8 samples


def _find_length_match(sample: dict, records: list[ExtractRecord], used: set[int]) -> tuple[ExtractRecord | None, str, str]:
    xml_len = int(sample.get("sample_length") or 0)
    xml_rate = int(sample.get("sample_rate") or 0)

    if xml_len <= 0:
        return None, "length_missing_xml", "none"

    candidates: list[tuple[int, ExtractRecord]] = []
    for record in records:
        if record.bank_index in used:
            continue
        if record.frames <= 0:
            continue
        if xml_rate and record.samplerate and xml_rate != record.samplerate:
            continue
        delta = abs(xml_len - record.frames)
        tol = _length_tolerance(xml_len)
        if delta <= tol:
            candidates.append((delta, record))

    candidates.sort(key=lambda x: (x[0], x[1].bank_index))

    if len(candidates) == 1:
        delta, record = candidates[0]
        confidence = "high" if delta == 0 else "medium"
        method = "length_exact" if delta == 0 else "length_near"
        return record, method, confidence

    if len(candidates) > 1:
        # Try to break ties using old semantic hints if available.
        sound_key = normalize_key(sample.get("sound_name", ""))
        title_key = normalize_key(sample.get("original_display_name", ""))
        artist_title_key = compact_artist_title(sample.get("original_artist", ""), sample.get("original_display_name", ""))

        for key, method in [
            (sound_key, "length_tie_sound_name"),
            (artist_title_key, "length_tie_artist_title"),
            (title_key, "length_tie_title"),
        ]:
            if not key:
                continue
            narrowed = []
            for delta, record in candidates:
                rkey = record.match_key
                if rkey and (key == rkey or key in rkey or rkey in key):
                    narrowed.append((delta, record))
            if len(narrowed) == 1:
                return narrowed[0][1], method, "medium"

        return None, f"length_ambiguous_{len(candidates)}", "none"

    return None, "length_no_match", "none"


def _match_samples_to_records(samples: list[dict], records: list[ExtractRecord]) -> dict[int, tuple[ExtractRecord | None, str, str]]:
    """Return slot_index -> (ExtractRecord|None, match_method, confidence).

    v3.0 priority:
    1. XML SampleLength/SampleRate vs extracted wav frames/samplerate.
    2. Semantic filename hints only as tie-breaker.
    3. No silent fallback when a full Extract template exists.
    """
    result: dict[int, tuple[ExtractRecord | None, str, str]] = {}
    used: set[int] = set()

    # 1. Length-based matching. This handles sound_0.wav / sound_1.wav names.
    for sample in samples:
        slot = int(sample["slot_index"])
        record, method, confidence = _find_length_match(sample, records, used)
        if record:
            result[slot] = (record, method, confidence)
            used.add(record.bank_index)
        else:
            result[slot] = (None, method, confidence)

    # 2. For still-unmatched slots, try older semantic matching only when filenames contain hints.
    candidates = _build_record_match_lists(records)

    def try_semantic(slot: int, key: str, method: str, confidence: str, contains: bool = False, min_len: int = 5) -> bool:
        if not key:
            return False
        for item in candidates:
            record = item["record"]
            if record.bank_index in used:
                continue
            rkey = item["key"]
            if not rkey:
                continue
            ok = (rkey == key) if not contains else (len(key) >= min_len and (key in rkey or rkey in key))
            if ok:
                result[slot] = (record, method, confidence)
                used.add(record.bank_index)
                return True
        return False

    for sample in samples:
        slot = int(sample["slot_index"])
        if result.get(slot, (None, "", ""))[0] is not None:
            continue
        sound_key = normalize_key(sample.get("sound_name", ""))
        if try_semantic(slot, sound_key, "sound_name_exact", "high"):
            continue
        if try_semantic(slot, sound_key, "sound_name_contains", "medium", contains=True):
            continue
        title_key = normalize_key(sample.get("original_display_name", ""))
        artist_title_key = compact_artist_title(sample.get("original_artist", ""), sample.get("original_display_name", ""))
        if try_semantic(slot, artist_title_key, "artist_title", "medium", contains=True, min_len=6):
            continue
        try_semantic(slot, title_key, "title_contains", "low", contains=True, min_len=5)

    # 3. Anything still unmatched remains unmatched. Do not silently fallback by same index.
    for sample in samples:
        slot = int(sample["slot_index"])
        if result.get(slot, (None, "", ""))[0] is None:
            old_method = result.get(slot, (None, "unmatched", "none"))[1]
            result[slot] = (None, old_method or "unmatched", "none")

    return result


def ensure_track_order_file(
    path: Path,
    xml_path: Path,
    station_name: str,
    adopted_files: list[str],
    metadata_path: Path | None = None,
    extract_template_dir: Path | None = None,
) -> Path:
    """创建/刷新槽位映射表。

    v2.9：
    - 导入完整 Fmod Extract wav 目录，而非单个 txt；
    - XML 按 slot_index 写显示信息；
    - Fmod rebuild workspace 按原 Extract txt/目录结构替换 wav 内容；
    - 无法匹配时标为 unmatched，并阻止生成错误映射。
    """
    existing = {row.slot_index: row for row in read_track_order(path)}
    meta = load_track_metadata(metadata_path) if metadata_path else {}

    samples = station_sample_rows(xml_path, station_name)
    records = parse_extract_template(extract_template_dir) if extract_template_dir else []
    match_map = _match_samples_to_records(samples, records) if records else {}

    rows: list[TrackOrderRow] = []

    for sample in samples:
        slot = int(sample["slot_index"])
        old = existing.get(slot)

        if old:
            audio_filename = old.audio_filename
            display_name = old.display_name
            artist = old.artist
            notes = old.notes
        else:
            audio_filename = adopted_files[slot] if slot < len(adopted_files) else ""
            m = meta.get(audio_filename)
            if m:
                display_name, artist = m.display_name, m.artist
            elif audio_filename:
                display_name, artist = guess_display_artist_from_filename(audio_filename)
            else:
                display_name, artist = sample["original_display_name"], sample["original_artist"]
            notes = ""

        if records:
            record, match_method, confidence = match_map.get(slot, (None, "unmatched", "none"))
            if record:
                bank_index = record.bank_index
                fsb_txt = record.txt_relpath
                subsound_index = record.subsound_index
                extracted_name = record.extracted_name
                original_wav_relpath = record.original_relpath
                extract_frames = record.frames
                extract_sample_rate = record.samplerate
                length_delta = abs(int(sample.get("sample_length") or 0) - record.frames) if record.frames else 0
            else:
                bank_index = -1
                fsb_txt = ""
                subsound_index = -1
                extracted_name = ""
                original_wav_relpath = ""
                extract_frames = 0
                extract_sample_rate = 0
                length_delta = 0
                notes = (notes + " | " if notes else "") + "无法从 Fmod Extract 模板自动匹配，请检查 SampleLength/提取目录。"
        elif old:
            bank_index = old.bank_index
            fsb_txt = old.fsb_txt
            subsound_index = old.subsound_index
            extracted_name = old.extracted_name
            original_wav_relpath = old.original_wav_relpath
            extract_frames = old.extract_frames
            extract_sample_rate = old.extract_sample_rate
            length_delta = old.length_delta
            match_method = old.match_method or "existing_no_template"
            confidence = old.confidence or "unknown"
        else:
            bank_index = slot
            fsb_txt = ""
            subsound_index = slot
            extracted_name = ""
            original_wav_relpath = ""
            extract_frames = 0
            extract_sample_rate = 0
            length_delta = 0
            match_method = "no_extract_template_same_index"
            confidence = "unsafe"

        rows.append(
            TrackOrderRow(
                slot_index=slot,
                bank_index=bank_index,
                fsb_txt=fsb_txt,
                subsound_index=subsound_index,
                sound_name=sample["sound_name"],
                original_display_name=sample["original_display_name"],
                original_artist=sample["original_artist"],
                extracted_name=extracted_name,
                original_wav_relpath=original_wav_relpath,
                xml_sample_length=int(sample.get("sample_length") or 0),
                xml_sample_rate=int(sample.get("sample_rate") or 0),
                extract_frames=extract_frames,
                extract_sample_rate=extract_sample_rate,
                length_delta=length_delta,
                audio_filename=audio_filename,
                display_name=display_name,
                artist=artist,
                match_method=match_method,
                confidence=confidence,
                notes=notes,
            )
        )

    write_track_order(path, rows)
    return path


def validate_track_order(rows: list[TrackOrderRow], audio_by_filename: dict[str, AudioInfo]) -> list[str]:
    errors: list[str] = []
    used_audio: set[str] = set()
    used_bank: set[int] = set()

    for row in rows:
        if row.audio_filename:
            if row.audio_filename not in audio_by_filename:
                errors.append(f"slot {row.slot_index}: audio_filename={row.audio_filename} 不在当前采用音频列表中")
            if row.audio_filename in used_audio:
                errors.append(f"audio_filename={row.audio_filename} 被重复使用。若不是刻意复用，请检查 track_order.csv。")
            used_audio.add(row.audio_filename)

        # 有导入模板时，无法匹配会是 -1/unmatched，必须阻止错误生成。
        if row.match_method == "unmatched" or row.bank_index < 0:
            errors.append(
                f"slot {row.slot_index}: 无法自动匹配 XML SoundName={row.sound_name} 到 Fmod Extract 结果。"
                "请确认导入的是该电台对应 bank 的完整 Wav Output Directory，且提取 wav 可读取；v3.0 会优先用 SampleLength 匹配。"
            )
            continue

        if row.bank_index in used_bank:
            errors.append(f"bank_index={row.bank_index} 被多个 XML 槽位使用，请检查 Fmod Extract 模板或 track_order.csv。")
        used_bank.add(row.bank_index)

    return errors


def rows_to_metadata(rows: list[TrackOrderRow]) -> dict[str, TrackMetadata]:
    result: dict[str, TrackMetadata] = {}
    for row in rows:
        if not row.audio_filename:
            continue
        result[row.audio_filename] = TrackMetadata(
            filename=row.audio_filename,
            display_name=row.display_name or Path(row.audio_filename).stem,
            artist=row.artist or "User",
        )
    return result


def create_fmod_rebuild_workspace(
    output_dir: Path,
    extract_template_dir: Path,
    rows: list[TrackOrderRow],
    audio_by_filename: dict[str, AudioInfo],
) -> Path:
    """生成用户无感的 Fmod Rebuild 输入，并自动匹配音量。"""
    output_dir = Path(output_dir)

    ready_wav_dir = output_dir / FMOD_READY_WAV_DIR_NAME
    workspace = output_dir / FMOD_REBUILD_WORKSPACE_DIR_NAME

    if ready_wav_dir.exists():
        shutil.rmtree(ready_wav_dir, ignore_errors=True)
    if workspace.exists():
        shutil.rmtree(workspace, ignore_errors=True)

    bank_dir = workspace / "bank"
    build_dir = workspace / "build"
    cache_dir = workspace / "fsbcache"

    bank_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    if extract_template_dir and Path(extract_template_dir).exists():
        shutil.copytree(extract_template_dir, ready_wav_dir)
    else:
        ready_wav_dir.mkdir(parents=True, exist_ok=True)

    replaced_count = 0
    volume_rows: list[dict[str, object]] = []

    for row in rows:
        if not row.audio_filename or not row.original_wav_relpath:
            continue
        info = audio_by_filename.get(row.audio_filename)
        if not info:
            continue

        src = Path(info.path)
        if not src.exists():
            continue

        dst = ready_wav_dir / row.original_wav_relpath
        reference = dst

        if not reference.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            volume_rows.append({
                "slot_index": row.slot_index,
                "bank_index": row.bank_index,
                "audio_filename": row.audio_filename,
                "target_wav": row.original_wav_relpath,
                "source_dbfs": "",
                "reference_dbfs": "",
                "requested_gain_db": "",
                "applied_gain_db": "",
                "peak_limited": "",
                "status": "copied_no_reference",
            })
            replaced_count += 1
            continue

        # 先以原 sound_x.wav 为参考音量，再覆盖写入用户音乐。
        result = match_loudness_to_reference(src, reference, dst)
        volume_rows.append({
            "slot_index": row.slot_index,
            "bank_index": row.bank_index,
            "audio_filename": row.audio_filename,
            "target_wav": row.original_wav_relpath,
            "source_dbfs": "" if result.source_dbfs is None else f"{result.source_dbfs:.3f}",
            "reference_dbfs": "" if result.reference_dbfs is None else f"{result.reference_dbfs:.3f}",
            "requested_gain_db": f"{result.requested_gain_db:.3f}",
            "applied_gain_db": f"{result.applied_gain_db:.3f}",
            "peak_limited": str(result.peak_limited),
            "status": result.status,
        })
        replaced_count += 1

    volume_report = output_dir.parent / "work" / "volume_match_report.csv"
    volume_report.parent.mkdir(parents=True, exist_ok=True)
    with volume_report.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "slot_index",
            "bank_index",
            "audio_filename",
            "target_wav",
            "source_dbfs",
            "reference_dbfs",
            "requested_gain_db",
            "applied_gain_db",
            "peak_limited",
            "status",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(volume_rows)

    user_readme = output_dir.parent / "work" / "READ_ME_REBUILD_BANK.txt"
    user_readme.write_text(
        "\n".join([
            "FH6 Radio Tool v3.2 - 最终重构说明",
            "=" * 60,
            "",
            "已完成：映射校准 / 音频替换 / 音量匹配",
            "",
            "Fmod Bank Tools Directory Settings：",
            f"Bank Input Directory:   {bank_dir}",
            f"Wav Output Directory:   {ready_wav_dir}",
            f"Build Output Directory: {build_dir}",
            f"Cache Directory:        {cache_dir}",
            "",
            "操作：",
            "1. 把原游戏对应的 .bank 文件复制到 Bank Input Directory。",
            "2. 在 Fmod Bank Tools 中执行 Rebuild。",
            "3. 从 Build Output Directory 取新生成的 .bank。",
            "4. 用新 .bank 和 output/RadioInfo_CN.xml 替换游戏文件。",
            "",
            f"已替换音频数量：{replaced_count}",
            "音量报告：output/volume_match_report.csv",
        ]) + "\n",
        encoding="utf-8",
    )

    readme = workspace / "README_FMOD_DIRECTORY_SETTINGS.txt"
    readme.write_text(
        "\n".join([
            "Fmod Bank Tools Directory Settings",
            "=" * 60,
            "",
            f"Bank Input Directory:   {bank_dir}",
            f"Wav Output Directory:   {ready_wav_dir}",
            f"Build Output Directory: {build_dir}",
            f"Cache Directory:        {cache_dir}",
            "",
            "请把原游戏对应的 bank 文件放入 bank/，然后执行 Rebuild。",
            "重构后的 bank 会出现在 build/。",
        ]) + "\n",
        encoding="utf-8",
    )

    return workspace
