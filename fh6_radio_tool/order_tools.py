from __future__ import annotations

import csv
import re
import shutil
import wave
from dataclasses import dataclass
from pathlib import Path

from .runtime_tools import bundled_resource_root, runtime_root

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



def _candidate_config_paths(filename: str) -> list[Path]:
    """Return editable/bundled config locations in priority order."""
    roots: list[Path] = []
    for root in (runtime_root(), bundled_resource_root(), Path.cwd(), Path(__file__).resolve().parents[1]):
        try:
            root = Path(root).resolve()
        except Exception:
            root = Path(root)
        if root not in roots:
            roots.append(root)
    return [root / "config" / filename for root in roots]


def _load_cross_bank_overrides() -> list[dict[str, str]]:
    """Load manually confirmed cross-bank mappings.

    These mappings are intentionally data-driven because FH6 can expose RadioInfo
    rows whose real sample data may live outside the station's main Tracks bank.
    Only manually confirmed rows should be placed in this CSV.  Unconfirmed
    candidates must remain in developer search reports.
    """
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for path in _candidate_config_paths("known_cross_bank_music_map.csv"):
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    enabled = _clean(row.get("enabled", "1")).lower()
                    if enabled in {"0", "false", "no", "off"}:
                        continue
                    item = {
                        "station_number": _clean(row.get("station_number", "")),
                        "slot_index": _clean(row.get("slot_index", "")),
                        "sound_name": _clean(row.get("sound_name", "")),
                        "candidate_bank": _clean(row.get("candidate_bank", "")),
                        "candidate_sound": _clean(row.get("candidate_sound", "")),
                        "note": _clean(row.get("note", "")),
                    }
                    if not item["candidate_bank"] or not item["candidate_sound"]:
                        continue
                    key = (item["station_number"], item["slot_index"], item["sound_name"].lower(), item["candidate_sound"].lower())
                    if key not in seen:
                        seen.add(key)
                        out.append(item)
        except Exception:
            continue
    return out


def _override_matches_sample(item: dict[str, str], sample: dict) -> bool:
    slot_text = str(sample.get("slot_index", ""))
    if item.get("slot_index") and item["slot_index"] != slot_text:
        return False
    if item.get("sound_name"):
        return item["sound_name"].lower() == str(sample.get("sound_name") or "").lower()
    return bool(item.get("slot_index"))


def _find_record_for_cross_bank_override(item: dict[str, str], records: list[ExtractRecord], used: set[int]) -> ExtractRecord | None:
    bank_want = item.get("candidate_bank", "").lower().replace(".bank", "").replace(".assets", "")
    sound_want = item.get("candidate_sound", "").lower()
    sound_stem = Path(sound_want).stem.lower()
    candidates: list[ExtractRecord] = []
    for record in records:
        if record.bank_index in used:
            continue
        bank_text = (record.txt_relpath + " " + record.original_relpath).lower().replace(".bank", "").replace(".assets", "")
        if bank_want and bank_want not in bank_text:
            continue
        if record.extracted_name.lower() == sound_want or record.extracted_stem.lower() == sound_stem or record.original_relpath.lower().endswith(sound_want):
            candidates.append(record)
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        candidates.sort(key=lambda r: (r.txt_relpath.lower(), r.subsound_index, r.bank_index))
        return candidates[0]
    return None

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

    # v3.0.10: do an explicit exact-length pass before building the tolerance
    # window.  This is intentionally duplicated instead of relying only on the
    # later candidates list so regressions cannot reintroduce the R6/Technopolis
    # failure where sound_23.wav was an exact match but sound_10.wav was close
    # enough to produce length_ambiguous_2.
    exact_records: list[ExtractRecord] = []
    for record in records:
        if record.bank_index in used:
            continue
        if record.frames <= 0:
            continue
        if xml_rate and record.samplerate and xml_rate != record.samplerate:
            continue
        if int(record.frames) == xml_len:
            exact_records.append(record)
    if len(exact_records) == 1:
        return exact_records[0], "length_exact", "high"
    if len(exact_records) > 1:
        # Multiple original sounds can share the same exact frame count.  Prefer
        # an unused record whose numeric sound_N matches the XML slot, then fall
        # back to the existing semantic tie-breakers below.
        try:
            slot_index = int(sample.get("slot_index") or -1)
        except Exception:
            slot_index = -1
        for record in exact_records:
            if record.subsound_index == slot_index:
                return record, "length_exact_same_index_tie", "medium"

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

    exact_candidates = [(delta, record) for delta, record in candidates if delta == 0]
    if len(exact_candidates) == 1:
        return exact_candidates[0][1], "length_exact", "high"
    if len(exact_candidates) > 1:
        candidates = exact_candidates

    if len(candidates) == 1:
        delta, record = candidates[0]
        confidence = "high" if delta == 0 else "medium"
        method = "length_exact" if delta == 0 else "length_near"
        return record, method, confidence

    if len(candidates) > 1:
        if candidates[0][0] > 0 and candidates[0][0] < candidates[1][0]:
            best_delta, best_record = candidates[0]
            second_delta = candidates[1][0]
            one_second = int(xml_rate or 48000)
            if second_delta >= best_delta * 4 or second_delta - best_delta >= one_second:
                return best_record, "length_near_best_unique", "medium"

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


def _record_text_key(record: ExtractRecord) -> str:
    return " ".join([record.txt_relpath, record.txt_stem, record.extracted_name, record.extracted_stem]).lower()


def _infer_station_token_from_samples(samples: list[dict]) -> str:
    counts: dict[str, int] = {}
    for sample in samples:
        text = str(sample.get("sound_name") or "").lower()
        m = re.search(r"(?:^|_)r(\d+)(?:_|$)", text)
        if m:
            token = "r" + m.group(1)
            counts[token] = counts.get(token, 0) + 1
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _partition_records_for_station(samples: list[dict], records: list[ExtractRecord]) -> tuple[list[ExtractRecord], list[ExtractRecord], list[ExtractRecord]]:
    """Return (primary, supplemental_glb_radio_3d, other) records.

    RadioInfo rows normally map to the station's own R*_Tracks bank.  Some rows
    may be stored in shared/global banks, but those cross-bank mappings must be
    confirmed before they are used for normal replacement.  Keep shared-bank
    records separated so they do not steal ordinary station slots.
    """
    token = _infer_station_token_from_samples(samples)
    primary: list[ExtractRecord] = []
    glb3d: list[ExtractRecord] = []
    other: list[ExtractRecord] = []
    for record in records:
        key = _record_text_key(record)
        if "glb_radio_3d" in key:
            glb3d.append(record)
        elif token and token in key and "tracks" in key:
            primary.append(record)
        else:
            other.append(record)
    if not primary:
        primary = [r for r in records if r not in glb3d]
    return primary, glb3d, other


def _find_supplemental_glb_match(sample: dict, records: list[ExtractRecord], used: set[int]) -> tuple[ExtractRecord | None, str, str]:
    if not records:
        return None, "supplemental_glb_radio_3d_no_records", "none"
    record, method, confidence = _find_length_match(sample, records, used)
    if record:
        return record, "supplemental_glb_radio_3d_" + method, "medium" if confidence in {"high", "medium"} else "low"
    return None, method, confidence


def _record_bank_group_key(record: ExtractRecord) -> str:
    """Return the extracted bank folder key for a record.

    Fmod Bank Tools outputs paths like::

        R1_Tracks_CU1.assets[0]/R1_Tracks_CU1.assets[0].txt
        R1_Tracks_Disk.assets[0]/R1_Tracks_Disk.assets[0].txt

    For multi-track-bank stations we must keep CU1 and Disk as separate
    ordered groups rather than letting length matching consume Disk sounds for
    the wrong XML rows.
    """
    text = str(record.txt_relpath or record.original_relpath or "")
    if "/" in text:
        return text.split("/", 1)[0]
    if "\\" in text:
        return text.split("\\", 1)[0]
    return str(record.txt_stem or record.extracted_stem or "")


def _track_bank_group_sort_key(group_key: str):
    name = str(group_key or "").lower().replace("-", "_")
    score = 100
    if "tracks" in name:
        score -= 50
    if "cu1" in name:
        score -= 30
    if "disk" in name:
        score -= 20
    if "stinger" in name or "dj" in name or name.startswith("vo_"):
        score += 1000
    return (score, natural_key(name))


def _group_track_records(records: list[ExtractRecord]) -> list[tuple[str, list[ExtractRecord]]]:
    groups: dict[str, list[ExtractRecord]] = {}
    for record in records:
        if record.frames <= 0:
            continue
        key = _record_bank_group_key(record)
        groups.setdefault(key, []).append(record)
    out = []
    for key, items in groups.items():
        items = sorted(items, key=lambda r: (r.subsound_index, r.bank_index))
        out.append((key, items))
    out.sort(key=lambda kv: _track_bank_group_sort_key(kv[0]))
    return out


def _flatten_groups(groups: list[tuple[str, list[ExtractRecord]]]) -> list[ExtractRecord]:
    out: list[ExtractRecord] = []
    for _key, items in groups:
        out.extend(items)
    return out


def _find_tiered_track_bank_match(
    sample: dict,
    primary_records: list[ExtractRecord],
    supplemental_track_records: list[ExtractRecord],
    glb_records: list[ExtractRecord],
    used: set[int],
) -> tuple[ExtractRecord | None, str, str]:
    """Find a match using the safer FH radio bank priority order.

    Priority:
    1. Current station primary Track bank, normally R*_Tracks_CU1.
    2. Other same-station R*_Tracks_* banks, e.g. CU2/Disk.
    3. GLB_Radio_3D as low-confidence fallback only when it was explicitly
       included in the extract template.

    This prevents unrelated radio main banks from stealing slots while still
    letting FH's split-bank station structure resolve missing songs.
    """
    record, method, confidence = _find_length_match(sample, primary_records, used)
    if record:
        return record, method, confidence

    record, method2, confidence2 = _find_length_match(sample, supplemental_track_records, used)
    if record:
        conf = "medium" if confidence2 in {"high", "medium"} else "low"
        return record, "same_station_extra_bank_" + method2, conf

    record, method3, confidence3 = _find_length_match(sample, glb_records, used)
    if record:
        # GLB_Radio_3D matches are useful candidates but should never look as
        # trustworthy as direct same-station Track-bank matches.
        conf = "low" if confidence3 in {"high", "medium"} else "none"
        return record, "glb_radio_3d_candidate_" + method3, conf

    # Preserve the most informative failure reason.
    return None, method2 if supplemental_track_records else method, "none"


def _preassign_multi_track_bank_tail(
    samples: list[dict],
    records: list[ExtractRecord],
    used: set[int],
) -> dict[int, tuple[ExtractRecord | None, str, str]]:
    """Map supplemental same-station Track banks by bank order.

    FH6 stations can split their music across multiple R*_Tracks_* banks.  R1 is
    the concrete case that exposed this: R1_Tracks_CU1 has the ordinary songs,
    while R1_Tracks_Disk contains the remaining songs/variants.  Their
    RadioInfo SampleLength values are not always reliable enough for automatic
    length matching, so the supplemental Track bank must be assigned by its
    position after the main CU1 bank.

    This function only preassigns slots that fall after the first Track bank's
    extracted sound count, and only when the combined Track-bank record count
    can cover the XML rows.  It never guesses across unrelated global banks.
    """
    result: dict[int, tuple[ExtractRecord | None, str, str]] = {}
    ordered_samples = sorted(samples, key=lambda x: int(x.get("slot_index", 0)))
    if not ordered_samples:
        return result
    groups = _group_track_records(records)
    if len(groups) < 2:
        return result
    total_records = sum(len(items) for _, items in groups)
    if total_records < len(ordered_samples):
        return result

    start = len(groups[0][1])
    for group_index, (group_key, items) in enumerate(groups[1:], start=1):
        for offset, record in enumerate(items):
            pos = start + offset
            if pos >= len(ordered_samples):
                break
            slot = int(ordered_samples[pos].get("slot_index", pos))
            if record.bank_index in used:
                continue
            result[slot] = (record, "multi_track_bank_order_tail", "medium")
            used.add(record.bank_index)
        start += len(items)
    return result


def _match_samples_to_records(samples: list[dict], records: list[ExtractRecord]) -> dict[int, tuple[ExtractRecord | None, str, str]]:
    """Return slot_index -> (ExtractRecord|None, match_method, confidence).

    v3.0 priority:
    1. XML SampleLength/SampleRate vs extracted wav frames/samplerate.
    2. Semantic filename hints only as tie-breaker.
    3. No silent fallback when a full Extract template exists.
    """
    result: dict[int, tuple[ExtractRecord | None, str, str]] = {}
    used: set[int] = set()
    primary_records, supplemental_glb_records, _other_records = _partition_records_for_station(samples, records)
    matching_records = primary_records or records
    track_groups = _group_track_records(matching_records)
    primary_track_records = track_groups[0][1] if track_groups else matching_records
    supplemental_track_records = _flatten_groups(track_groups[1:]) if len(track_groups) > 1 else []

    # 0. Do NOT preassign supplemental Track banks by tail position before
    # exact matching.  R1 exposed why this is unsafe: R1_Tracks_Disk contains
    # four files, but XML has duplicate non-ID/ID rows and short FI/LI rows.
    # Positional tail mapping can shift Disk sound_0/1 onto the wrong XML rows.
    # Always try exact/near SampleLength matching by bank priority first, then
    # fall back only for still-unmatched rows.

    # 1. Tiered matching: primary Track bank -> same-station extra Track banks
    # -> GLB_Radio_3D candidate.  This is the safe form of the user's requested
    # search order and avoids unrelated R2/R6/R9 main-bank length coincidences.
    for sample in samples:
        slot = int(sample["slot_index"])
        if result.get(slot, (None, "", ""))[0] is not None:
            continue
        record, method, confidence = _find_tiered_track_bank_match(
            sample, primary_track_records, supplemental_track_records, supplemental_glb_records, used
        )
        if record:
            result[slot] = (record, method, confidence)
            used.add(record.bank_index)
        else:
            result[slot] = (None, method, confidence)

    # 2. For still-unmatched slots, try older semantic matching only when filenames contain hints.
    candidates = _build_record_match_lists(matching_records)

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

    # 3. Stubborn-slot rescue fallback.
    #
    # Some FH radio banks contain a few XML slots whose SampleLength does not
    # exactly match the wav extracted by Fmod Bank Tools.  Older versions left
    # those rows as unmatched, so XML titles were updated while the actual bank
    # audio stayed original.  As a safer recovery step, try an order-based song
    # inventory fallback.  The method is explicitly marked as low confidence in
    # track_order.csv / replacement_plan.csv so it is not silent.
    probable_records = [
        r for r in matching_records
        if r.frames > 0 and r.samplerate > 0 and r.duration_sec >= 20.0
    ]
    probable_records.sort(key=lambda r: r.bank_index)
    ordered_samples = sorted(samples, key=lambda x: int(x["slot_index"]))

    if len(probable_records) >= len(ordered_samples):
        for pos, sample in enumerate(ordered_samples):
            slot = int(sample["slot_index"])
            if result.get(slot, (None, "", ""))[0] is not None:
                continue
            if pos >= len(probable_records):
                continue
            record = probable_records[pos]
            if record.bank_index in used:
                continue
            result[slot] = (record, "probable_song_order_fallback", "low")
            used.add(record.bank_index)

    # 4. Last-resort same-index fallback.  This is still better than silently
    # skipping a selected slot, but remains low confidence and is visible in the
    # generated diagnostics.
    for sample in ordered_samples:
        slot = int(sample["slot_index"])
        if result.get(slot, (None, "", ""))[0] is not None:
            continue
        if 0 <= slot < len(matching_records):
            record = matching_records[slot]
            if record.bank_index not in used and record.frames > 0:
                result[slot] = (record, "same_index_fallback", "low")
                used.add(record.bank_index)

    # 5. Data-driven cross-bank overrides.
    # Only mappings that have been manually confirmed should be enabled in
    # config/known_cross_bank_music_map.csv.  The default file is intentionally
    # empty after the R1/GLB_Radio_3D candidates were disproved by listening tests.
    overrides = _load_cross_bank_overrides()
    for sample in samples:
        slot = int(sample["slot_index"])
        if result.get(slot, (None, "", ""))[0] is not None:
            continue
        for item in overrides:
            if not _override_matches_sample(item, sample):
                continue
            record = _find_record_for_cross_bank_override(item, records, used)
            if record:
                result[slot] = (record, "cross_bank_override", "high")
                used.add(record.bank_index)
                break

    # 6. Cross-bank supplemental fallback.
    # This normally does nothing unless the caller intentionally included shared
    # bank records in the Extract template.  It remains diagnostic/fallback code;
    # normal station profiles should not expose unconfirmed shared-bank rows.
    for sample in samples:
        slot = int(sample["slot_index"])
        if result.get(slot, (None, "", ""))[0] is not None:
            continue
        record, method, confidence = _find_supplemental_glb_match(sample, supplemental_glb_records, used)
        if record:
            result[slot] = (record, method, confidence)
            used.add(record.bank_index)

    # 7. Anything still unmatched remains unmatched.
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


def write_fmod_sound_inventory(path: Path, extract_template_dir: Path | None) -> Path:
    """Write a diagnostic list of all sounds found in the FMOD Extract template."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    records = parse_extract_template(extract_template_dir) if extract_template_dir else []
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "bank_index", "fsb_txt", "subsound_index", "extracted_name",
            "original_wav_relpath", "frames", "samplerate", "duration_sec",
            "probable_song", "match_key",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow({
                "bank_index": record.bank_index,
                "fsb_txt": record.txt_relpath,
                "subsound_index": record.subsound_index,
                "extracted_name": record.extracted_name,
                "original_wav_relpath": record.original_relpath,
                "frames": record.frames,
                "samplerate": record.samplerate,
                "duration_sec": f"{record.duration_sec:.3f}",
                "probable_song": str(record.duration_sec >= 20.0),
                "match_key": record.match_key,
            })
    return path


def write_replacement_plan(
    path: Path,
    rows: list[TrackOrderRow],
    audio_by_filename: dict[str, AudioInfo],
    assigned_slots,
    extract_template_dir: Path | None = None,
) -> Path:
    """Write the XML slot -> FMOD sound -> user music plan used for this run."""
    assigned = {int(x) for x in assigned_slots}
    row_by_slot = {row.slot_index: row for row in rows}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "selected", "status", "reason",
            "slot_index", "bank_index", "fsb_txt", "subsound_index",
            "sound_name", "original_display_name", "original_artist",
            "target_display_name", "target_artist", "audio_filename",
            "resolved_wav_relpath", "resolved_wav_exists",
            "xml_sample_length", "extract_frames", "length_delta",
            "match_method", "confidence", "notes",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for slot in sorted(set(row_by_slot) | assigned):
            row = row_by_slot.get(slot)
            if row is None:
                writer.writerow({
                    "selected": str(slot in assigned),
                    "status": "error",
                    "reason": "selected XML slot is missing from track_order",
                    "slot_index": slot,
                })
                continue
            selected = row.slot_index in assigned
            reason_parts = []
            status = "not_selected"
            if selected:
                status = "ok"
                if not row.audio_filename:
                    status = "error"
                    reason_parts.append("no replacement audio assigned")
                elif row.audio_filename not in audio_by_filename:
                    status = "error"
                    reason_parts.append("assigned audio missing from prepared audio list")
                if row.bank_index < 0 or row.match_method == "unmatched" or not row.original_wav_relpath:
                    status = "error"
                    reason_parts.append("no matched FMOD sound for this XML slot")
                if row.confidence == "low":
                    reason_parts.append("low-confidence fallback mapping; verify in game")
            resolved_exists = ""
            if row.original_wav_relpath and extract_template_dir:
                resolved_exists = str((Path(extract_template_dir) / row.original_wav_relpath).exists())
            writer.writerow({
                "selected": str(selected),
                "status": status,
                "reason": "; ".join(reason_parts),
                "slot_index": row.slot_index,
                "bank_index": row.bank_index,
                "fsb_txt": row.fsb_txt,
                "subsound_index": row.subsound_index,
                "sound_name": row.sound_name,
                "original_display_name": row.original_display_name,
                "original_artist": row.original_artist,
                "target_display_name": row.display_name,
                "target_artist": row.artist,
                "audio_filename": row.audio_filename,
                "resolved_wav_relpath": row.original_wav_relpath,
                "resolved_wav_exists": resolved_exists,
                "xml_sample_length": row.xml_sample_length,
                "extract_frames": row.extract_frames,
                "length_delta": row.length_delta,
                "match_method": row.match_method,
                "confidence": row.confidence,
                "notes": row.notes,
            })
    return path


def validate_selected_replacements(
    rows: list[TrackOrderRow],
    audio_by_filename: dict[str, AudioInfo],
    assigned_slots,
    extract_template_dir: Path | None = None,
) -> list[str]:
    """Validate only the slots selected in the current replacement run.

    Unselected XML rows may remain unmatched; selected rows must all resolve to
    a real FMOD extracted wav and a prepared user audio file.  This prevents the
    dangerous state where XML titles are changed but bank audio is not replaced.
    """
    assigned = sorted({int(x) for x in assigned_slots})
    row_by_slot = {row.slot_index: row for row in rows}
    errors: list[str] = []
    warnings: list[str] = []
    used_bank: dict[int, int] = {}
    used_wav: dict[str, int] = {}

    for slot in assigned:
        row = row_by_slot.get(slot)
        if row is None:
            errors.append(f"slot {slot}: XML 槽位不存在，无法建立替换计划。")
            continue
        if not row.audio_filename:
            errors.append(f"slot {slot}: 当前替换计划中没有绑定用户音频。")
        elif row.audio_filename not in audio_by_filename:
            errors.append(f"slot {slot}: 已绑定音频 {row.audio_filename} 但准备音频列表中不存在。")

        if row.bank_index < 0 or row.match_method == "unmatched" or not row.original_wav_relpath:
            errors.append(
                f"slot {slot}: 无法匹配到 FMOD 提取音频。SoundName={row.sound_name or '-'}，"
                f"match={row.match_method or 'unmatched'}。"
            )
            continue

        if extract_template_dir:
            target = Path(extract_template_dir) / row.original_wav_relpath
            if not target.exists():
                errors.append(f"slot {slot}: 匹配到 {row.original_wav_relpath}，但 Extract 模板中找不到该 wav。")

        if row.bank_index in used_bank:
            errors.append(f"slot {slot}: 与 slot {used_bank[row.bank_index]} 使用同一个 FMOD bank_index={row.bank_index}。")
        else:
            used_bank[row.bank_index] = slot

        if row.original_wav_relpath:
            if row.original_wav_relpath in used_wav:
                errors.append(f"slot {slot}: 与 slot {used_wav[row.original_wav_relpath]} 使用同一个目标 wav={row.original_wav_relpath}。")
            else:
                used_wav[row.original_wav_relpath] = slot

        if row.confidence == "low":
            warnings.append(
                f"slot {slot}: 使用低置信度映射 {row.match_method} -> {row.original_wav_relpath}。"
            )

    return errors + ["WARN: " + item for item in warnings]


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
    progress_callback=None,
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

    if progress_callback:
        progress_callback(60, "Copying original FMOD Extract template...")

    if extract_template_dir and Path(extract_template_dir).exists():
        shutil.copytree(extract_template_dir, ready_wav_dir)
    else:
        ready_wav_dir.mkdir(parents=True, exist_ok=True)

    replaced_count = 0
    volume_rows: list[dict[str, object]] = []
    active_rows = [
        row for row in rows
        if row.audio_filename and row.original_wav_relpath and audio_by_filename.get(row.audio_filename)
    ]
    total_active = max(1, len(active_rows))
    processed_active = 0

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

        processed_active += 1
        if progress_callback:
            base = 62
            span = 30
            pct = base + int(span * min(processed_active, total_active) / total_active)
            progress_callback(
                pct,
                f"Replacing and loudness matching {processed_active}/{total_active}: {row.audio_filename}",
            )

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

    if replaced_count != len(active_rows):
        raise ValueError(
            f"FMOD 替换数量不一致：计划替换 {len(active_rows)} 个 wav，但实际只生成 {replaced_count} 个。"
            "为避免游戏里显示新歌名但实际仍播放原曲，已停止生成。"
        )

    if progress_callback:
        progress_callback(93, "Writing loudness report...")

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

    if progress_callback:
        progress_callback(95, "Writing rebuild instructions...")

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
