from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .segment_tools import MARKER_ORDER

IMPORT_COLUMNS = [
    "MatchName",
    "Filename",
    "DisplayName",
    "Artist",
    "SampleRate",
    "SampleLength",
    *MARKER_ORDER,
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
    **{m.lower(): m for m in MARKER_ORDER},
}

@dataclass(frozen=True)
class MarkerImportRow:
    source_row: int
    match_name: str
    filename: str
    display_name: str
    artist: str
    sample_rate: int
    sample_length: int
    markers: dict[str, int]


def normalize_match_text(value: object) -> str:
    text = str(value or "").strip().lower()
    if text.endswith(".wav") or text.endswith(".wave"):
        text = Path(text).stem.lower()
    text = re.sub(r"[\s_\-\.\(\)\[\]\{\}'\"，。、《》<>]+", "", text)
    return text


def _canon_header(header: str) -> str:
    h = str(header or "").strip().replace(" ", "")
    if h in IMPORT_COLUMNS:
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


def _row_from_dict(raw: dict[str, object], source_row: int) -> MarkerImportRow:
    row = {_canon_header(k): v for k, v in raw.items()}
    match_name = str(row.get("MatchName") or row.get("DisplayName") or row.get("Filename") or "").strip()
    filename = str(row.get("Filename") or "").strip()
    display_name = str(row.get("DisplayName") or match_name or Path(filename).stem).strip()
    artist = str(row.get("Artist") or "").strip()
    sample_rate = _parse_int(row.get("SampleRate"), 0)
    sample_length = _parse_int(row.get("SampleLength"), 0)
    markers = {m: _parse_int(row.get(m), -1) for m in MARKER_ORDER}
    # Keep TrackStart/End common defaults only when absent; do not invent loop markers.
    return MarkerImportRow(source_row, match_name, filename, display_name, artist, sample_rate, sample_length, markers)


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


def write_marker_import_template(path: Path, rows: Iterable[dict[str, object]] | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=IMPORT_COLUMNS)
        writer.writeheader()
        if rows:
            for row in rows:
                writer.writerow({col: row.get(col, "") for col in IMPORT_COLUMNS})
    return path
