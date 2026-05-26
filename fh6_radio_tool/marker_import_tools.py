from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .segment_tools import ADVANCED_DISABLE_MARKERS, ADVANCED_DISABLE_SENTINEL, MARKER_ORDER, NEGATIVE_SENTINEL_MARKERS

CONTEXT_COLUMNS = [
    "station",
    "radio",
    "station_name",
    "slot_index",
    "sound_name",
    "original_sound_name",
    "bank_name",
    "target_bank",
    "title",
    "display_name",
    "artist",
    "filename",
    "source_audio_path",
    "sample_rate",
    "sample_length",
    "duration_sec",
    "bpm",
    "marker_unit",
]

IMPORT_COLUMNS = [
    "MatchName",
    "Filename",
    "DisplayName",
    "Artist",
    "SampleRate",
    "SampleLength",
    *MARKER_ORDER,
]

EXPORT_COLUMNS = [
    *CONTEXT_COLUMNS,
    *MARKER_ORDER,
    "MatchName",
    "Filename",
    "DisplayName",
    "Artist",
    "SampleRate",
    "SampleLength",
]

_ALT_HEADERS = {
    "name": "MatchName",
    "song": "MatchName",
    "title": "MatchName",
    "displayname": "DisplayName",
    "display_name": "DisplayName",
    "filename": "Filename",
    "file": "Filename",
    "artist": "Artist",
    "samplerate": "SampleRate",
    "sample_rate": "SampleRate",
    "samplelength": "SampleLength",
    "sample_length": "SampleLength",
    "radio": "station",
    "stationname": "station_name",
    "station_name": "station_name",
    "slot": "slot_index",
    "slotindex": "slot_index",
    "slot_index": "slot_index",
    "soundname": "sound_name",
    "sound_name": "sound_name",
    "originalsoundname": "original_sound_name",
    "original_sound_name": "original_sound_name",
    "bank": "bank_name",
    "bankname": "bank_name",
    "bank_name": "bank_name",
    "targetbank": "target_bank",
    "target_bank": "target_bank",
    "sourceaudiopath": "source_audio_path",
    "source_audio_path": "source_audio_path",
    "duration": "duration_sec",
    "durationsec": "duration_sec",
    "duration_sec": "duration_sec",
    "markerunit": "marker_unit",
    "marker_unit": "marker_unit",
    **{m.lower(): m for m in MARKER_ORDER},
}

_CLEAR_TOKENS = {"clear", "null", "none", "delete", "remove", "<clear>"}
_DISABLE_TOKENS = {"disable", "disabled", "off", "<disable>"}

@dataclass(frozen=True)
class MarkerImportRow:
    source_row: int
    match_name: str
    filename: str
    display_name: str
    artist: str
    sample_rate: int
    sample_length: int
    markers: dict[str, int | None]
    station: str = ""
    slot_index: int | None = None
    sound_name: str = ""
    original_sound_name: str = ""
    bank_name: str = ""
    target_bank: str = ""
    source_audio_path: str = ""
    duration_sec: float = 0.0
    bpm: str = ""
    marker_unit: str = "samples"


def normalize_match_text(value: object) -> str:
    text = str(value or "").strip().lower()
    if text.endswith(".wav") or text.endswith(".wave"):
        text = Path(text).stem.lower()
    text = re.sub(r"[\s_\-\.\(\)\[\]\{\}'\"，。、《》<>]+", "", text)
    return text


def _canon_header(header: str) -> str:
    h = str(header or "").strip().replace(" ", "")
    if h in IMPORT_COLUMNS or h in CONTEXT_COLUMNS:
        return h
    key = h.lower().replace("-", "_")
    return _ALT_HEADERS.get(key, h)


def _parse_int(value: object, default: int = -1) -> int:
    if value is None:
        return default
    text = str(value).strip()
    if text == "":
        return default
    try:
        return int(float(text))
    except Exception:
        return default


def _parse_float(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    text = str(value).strip()
    if text == "":
        return default
    try:
        return float(text)
    except Exception:
        return default


def _parse_optional_int(value: object) -> int | None:
    text = str(value or "").strip()
    if text == "":
        return None
    try:
        return int(float(text))
    except Exception:
        return None


def _parse_marker_value(
    value: object,
    *,
    marker_name: str,
    marker_unit: str = "samples",
    sample_rate: int = 0,
) -> int | None | object:
    if value is None:
        return ...
    text = str(value).strip()
    if text == "":
        return ...
    if text.lower() in _CLEAR_TOKENS:
        return None
    if text.lower() in _DISABLE_TOKENS:
        if marker_name in ADVANCED_DISABLE_MARKERS:
            return ADVANCED_DISABLE_SENTINEL
        if marker_name in NEGATIVE_SENTINEL_MARKERS:
            return -1
        return None
    try:
        number = float(text)
    except Exception:
        return ...
    unit = str(marker_unit or "samples").strip().lower()
    if unit in {"second", "seconds", "sec", "secs", "s"} and sample_rate > 0:
        return int(round(number * int(sample_rate)))
    return int(round(number))


def _row_from_dict(raw: dict[str, object], source_row: int) -> MarkerImportRow:
    row = {_canon_header(k): v for k, v in raw.items()}
    station = str(row.get("station") or row.get("station_name") or row.get("radio") or "").strip()
    slot_index = _parse_optional_int(row.get("slot_index"))
    sound_name = str(row.get("sound_name") or "").strip()
    original_sound_name = str(row.get("original_sound_name") or sound_name).strip()
    bank_name = str(row.get("bank_name") or "").strip()
    target_bank = str(row.get("target_bank") or bank_name).strip()
    source_audio_path = str(row.get("source_audio_path") or "").strip()
    match_name = str(row.get("MatchName") or row.get("DisplayName") or row.get("Filename") or row.get("title") or "").strip()
    filename = str(row.get("Filename") or row.get("filename") or Path(source_audio_path).name or "").strip()
    display_name = str(row.get("DisplayName") or row.get("display_name") or row.get("title") or match_name or Path(filename).stem).strip()
    artist = str(row.get("Artist") or row.get("artist") or "").strip()
    sample_rate = _parse_int(row.get("SampleRate", row.get("sample_rate")), 0)
    sample_length = _parse_int(row.get("SampleLength", row.get("sample_length")), 0)
    duration_sec = _parse_float(row.get("duration_sec"), 0.0)
    bpm = str(row.get("bpm") or "").strip()
    marker_unit = str(row.get("marker_unit") or "samples").strip() or "samples"
    markers: dict[str, int | None] = {}
    for marker_name in MARKER_ORDER:
        if marker_name not in row:
            continue
        parsed = _parse_marker_value(
            row.get(marker_name),
            marker_name=marker_name,
            marker_unit=marker_unit,
            sample_rate=sample_rate,
        )
        if parsed is ...:
            continue
        markers[marker_name] = parsed  # None means explicit clear.
    return MarkerImportRow(
        source_row=source_row,
        match_name=match_name,
        filename=filename,
        display_name=display_name,
        artist=artist,
        sample_rate=sample_rate,
        sample_length=sample_length,
        markers=markers,
        station=station,
        slot_index=slot_index,
        sound_name=sound_name,
        original_sound_name=original_sound_name,
        bank_name=bank_name,
        target_bank=target_bank,
        source_audio_path=source_audio_path,
        duration_sec=duration_sec,
        bpm=bpm,
        marker_unit=marker_unit,
    )


def read_marker_import_file(path: Path) -> list[MarkerImportRow]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            data = data.get("tracks", [])
        if not isinstance(data, list):
            raise ValueError("JSON import file must be a list or an object with a 'tracks' list.")
        return [_row_from_dict(dict(item), i + 2) for i, item in enumerate(data) if isinstance(item, dict)]
    if suffix not in (".csv", ".txt"):
        raise ValueError("Marker import currently supports CSV or JSON files. Please use the provided CSV template.")
    rows: list[MarkerImportRow] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV file has no header row.")
        for idx, raw in enumerate(reader, start=2):
            if not any(str(v or "").strip() for v in raw.values()):
                continue
            rows.append(_row_from_dict(raw, idx))
    return rows


def write_marker_import_template(
    path: Path,
    rows: Iterable[dict[str, object]] | None = None,
    fieldnames: list[str] | None = None,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(fieldnames or IMPORT_COLUMNS)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        if rows:
            for row in rows:
                writer.writerow({col: row.get(col, "") for col in columns})
    return path
