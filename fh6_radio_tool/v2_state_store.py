from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

APP_VERSION = "2.7.3"
DB_FILE_NAME = "fh6_radio_tool_v2.sqlite3"


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def default_db_path(project_root: Path | None = None) -> Path:
    root = Path(project_root) if project_root else Path.cwd()
    return root / "work" / DB_FILE_NAME


@dataclass(frozen=True)
class TrackProfile:
    track_key: str
    source_path: str
    filename: str
    display_name: str = ""
    artist: str = ""
    sample_rate: int = 0
    sample_length: int = 0
    markers: dict[str, int] | None = None
    loop_candidates: list[dict[str, Any]] | None = None
    loudness: dict[str, Any] | None = None
    notes: str = ""


class StateStore:
    """SQLite project state used by FH6 Radio Tool v2.

    v1.x mainly persisted `segments.json` and `ui_session.json`.  v2 keeps a
    transactional SQLite store for assignments, loop candidates and deployment
    snapshots, while still exporting JSON sidecars for inspection and backup.
    """

    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS track_profiles (
                    track_key TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    artist TEXT NOT NULL DEFAULT '',
                    sample_rate INTEGER NOT NULL DEFAULT 0,
                    sample_length INTEGER NOT NULL DEFAULT 0,
                    markers_json TEXT NOT NULL DEFAULT '{}',
                    loop_candidates_json TEXT NOT NULL DEFAULT '[]',
                    loudness_json TEXT NOT NULL DEFAULT '{}',
                    notes TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS slot_assignments (
                    station_name TEXT NOT NULL,
                    slot_index INTEGER NOT NULL,
                    track_key TEXT NOT NULL,
                    original_sound_name TEXT NOT NULL DEFAULT '',
                    original_display_name TEXT NOT NULL DEFAULT '',
                    original_artist TEXT NOT NULL DEFAULT '',
                    match_confidence TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (station_name, slot_index)
                );

                CREATE TABLE IF NOT EXISTS deploy_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    game_root TEXT NOT NULL,
                    xml_path TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT ''
                );
                """
            )
            conn.execute("INSERT OR REPLACE INTO app_settings(key,value,updated_at) VALUES(?,?,?)", ("schema_version", "2", utc_now()))
            conn.execute("INSERT OR REPLACE INTO app_settings(key,value,updated_at) VALUES(?,?,?)", ("app_version", APP_VERSION, utc_now()))

    def set_setting(self, key: str, value: Any) -> None:
        text = json.dumps(value, ensure_ascii=False)
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO app_settings(key,value,updated_at) VALUES(?,?,?)",
                (key, text, utc_now()),
            )

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return default

    def save_track_profile(self, profile: TrackProfile) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO track_profiles(
                    track_key, source_path, filename, display_name, artist,
                    sample_rate, sample_length, markers_json, loop_candidates_json,
                    loudness_json, notes, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    profile.track_key,
                    profile.source_path,
                    profile.filename,
                    profile.display_name or Path(profile.filename).stem,
                    profile.artist,
                    int(profile.sample_rate or 0),
                    int(profile.sample_length or 0),
                    json.dumps(profile.markers or {}, ensure_ascii=False),
                    json.dumps(profile.loop_candidates or [], ensure_ascii=False),
                    json.dumps(profile.loudness or {}, ensure_ascii=False),
                    profile.notes,
                    utc_now(),
                ),
            )

    def load_track_profile(self, track_key: str) -> TrackProfile | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM track_profiles WHERE track_key=?", (track_key,)).fetchone()
        if not row:
            return None
        return self._row_to_profile(row)

    def list_track_profiles(self) -> list[TrackProfile]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM track_profiles ORDER BY filename COLLATE NOCASE").fetchall()
        return [self._row_to_profile(r) for r in rows]

    def _row_to_profile(self, row: sqlite3.Row) -> TrackProfile:
        def loads(name: str, default: Any):
            try:
                return json.loads(row[name])
            except Exception:
                return default
        return TrackProfile(
            track_key=row["track_key"],
            source_path=row["source_path"],
            filename=row["filename"],
            display_name=row["display_name"],
            artist=row["artist"],
            sample_rate=int(row["sample_rate"] or 0),
            sample_length=int(row["sample_length"] or 0),
            markers=loads("markers_json", {}),
            loop_candidates=loads("loop_candidates_json", []),
            loudness=loads("loudness_json", {}),
            notes=row["notes"],
        )

    def save_assignment(
        self,
        station_name: str,
        slot_index: int,
        track_key: str,
        original_sound_name: str = "",
        original_display_name: str = "",
        original_artist: str = "",
        match_confidence: str = "manual",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO slot_assignments(
                    station_name, slot_index, track_key, original_sound_name,
                    original_display_name, original_artist, match_confidence, updated_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    station_name,
                    int(slot_index),
                    track_key,
                    original_sound_name,
                    original_display_name,
                    original_artist,
                    match_confidence,
                    utc_now(),
                ),
            )

    def get_assignments(self, station_name: str) -> dict[int, str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT slot_index, track_key FROM slot_assignments WHERE station_name=?",
                (station_name,),
            ).fetchall()
        return {int(r["slot_index"]): r["track_key"] for r in rows}

    def clear_assignment(self, station_name: str, slot_index: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM slot_assignments WHERE station_name=? AND slot_index=?",
                (station_name, int(slot_index)),
            )

    def export_manifest(self, out_path: Path) -> Path:
        profiles = [asdict(p) for p in self.list_track_profiles()]
        with self.connect() as conn:
            assignments = [dict(r) for r in conn.execute("SELECT * FROM slot_assignments ORDER BY station_name, slot_index").fetchall()]
            settings = [dict(r) for r in conn.execute("SELECT * FROM app_settings ORDER BY key").fetchall()]
        data = {
            "schemaVersion": 2,
            "appVersion": APP_VERSION,
            "exportedAt": utc_now(),
            "settings": settings,
            "trackProfiles": profiles,
            "slotAssignments": assignments,
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return out_path
