from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg_tools import normalized_wav_path_for
from .wav_tools import list_audio_candidates, validate_wav


METADATA_FILE_NAME = "track_metadata.csv"


@dataclass(frozen=True)
class TrackMetadata:
    filename: str
    display_name: str
    artist: str


def _clean_text(text: str) -> str:
    text = str(text or "").strip()
    text = text.replace("\ufeff", "")
    return text


def guess_display_artist_from_filename(filename: str) -> tuple[str, str]:
    """从文件名猜测 DisplayName / Artist。

    支持常见格式：
    - Artist - Title.wav
    - Artist – Title.wav
    - Artist — Title.wav

    如果无法识别 Artist，则 DisplayName 使用文件名主体，Artist 使用 User。
    """
    stem = Path(filename).stem

    # 规范化文件名常带 _fh6_norm，显示名不应出现这个后缀。
    stem = re.sub(r"_fh6_norm$", "", stem, flags=re.IGNORECASE)

    # 去掉常见序号前缀。
    stem = re.sub(r"^\s*\d+[\s._-]+", "", stem)

    # 美化下划线。
    stem = stem.replace("_", " ").strip()

    for sep in [" - ", " – ", " — ", "-", "–", "—"]:
        if sep in stem:
            left, right = stem.split(sep, 1)
            artist = _clean_text(left)
            title = _clean_text(right)
            if artist and title:
                return title, artist

    return stem or Path(filename).stem, "User"


def expected_output_filename_for_source(source: Path) -> str:
    """不实际运行 FFmpeg，只判断当前源文件最终会被 txt/XML 使用的文件名。"""
    result = validate_wav(source)
    if result.ok:
        return source.name
    return normalized_wav_path_for(source).name


def load_track_metadata(path: Path) -> dict[str, TrackMetadata]:
    if not path.exists():
        return {}

    result: dict[str, TrackMetadata] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            filename = _clean_text(row.get("filename", ""))
            if not filename:
                continue
            display_name = _clean_text(row.get("display_name", "") or row.get("title", ""))
            artist = _clean_text(row.get("artist", ""))
            if not display_name:
                display_name, guessed_artist = guess_display_artist_from_filename(filename)
                artist = artist or guessed_artist
            if not artist:
                artist = "User"
            result[filename] = TrackMetadata(
                filename=filename,
                display_name=display_name,
                artist=artist,
            )
    return result


def write_track_metadata(path: Path, rows: list[TrackMetadata]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["filename", "display_name", "artist"])
        writer.writeheader()
        for item in rows:
            writer.writerow({
                "filename": item.filename,
                "display_name": item.display_name,
                "artist": item.artist,
            })


def ensure_metadata_file_for_filenames(path: Path, filenames: list[str]) -> Path:
    """创建或刷新 track_metadata.csv。

    保留用户已经编辑过的 display_name / artist；
    对新增文件按文件名自动猜测。
    """
    existing = load_track_metadata(path)
    rows: list[TrackMetadata] = []
    seen: set[str] = set()

    for filename in filenames:
        if filename in existing:
            rows.append(existing[filename])
        else:
            display_name, artist = guess_display_artist_from_filename(filename)
            rows.append(TrackMetadata(filename=filename, display_name=display_name, artist=artist))
        seen.add(filename)

    # 保留旧条目，防止用户临时移走文件时丢失编辑。
    for filename, item in existing.items():
        if filename not in seen:
            rows.append(item)

    write_track_metadata(path, rows)
    return path


def ensure_metadata_file_for_music_dir(music_dir: Path, slot_limit: int | None = None) -> Path:
    candidates = list_audio_candidates(music_dir)
    if slot_limit is not None:
        candidates = candidates[:slot_limit]
    filenames = [expected_output_filename_for_source(p) for p in candidates]
    return ensure_metadata_file_for_filenames(music_dir / METADATA_FILE_NAME, filenames)


def metadata_for_output_filenames(metadata_path: Path, filenames: list[str]) -> dict[str, TrackMetadata]:
    ensure_metadata_file_for_filenames(metadata_path, filenames)
    return load_track_metadata(metadata_path)


def ensure_metadata_file_for_music_dir_to_path(music_dir: Path, metadata_path: Path, slot_limit: int | None = None) -> Path:
    """从音乐文件夹扫描音频，但把 track_metadata.csv 写入指定位置。"""
    candidates = list_audio_candidates(music_dir)
    if slot_limit is not None:
        candidates = candidates[:slot_limit]
    filenames = [expected_output_filename_for_source(p) for p in candidates]
    return ensure_metadata_file_for_filenames(metadata_path, filenames)
