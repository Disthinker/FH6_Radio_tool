from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BackupEntry:
    original: str
    backup: str
    exists: bool


@dataclass(frozen=True)
class BackupSnapshot:
    snapshot_id: str
    root: str
    entries: list[BackupEntry]
    manifest_path: str


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe_name(path: Path) -> str:
    # Keep drive letters readable on Windows paths while avoiding nested copies.
    return str(path).replace(":", "").replace("\\", "/").strip("/").replace("/", "__")


def _sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            if chunk:
                h.update(chunk)
    return h.hexdigest()


def _game_key(game_root: Path | None) -> str:
    if game_root:
        src = str(Path(game_root).resolve()).lower()
    else:
        src = "unknown_game_root"
    return hashlib.sha1(src.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _state_root(backup_root: Path) -> Path:
    return Path(backup_root) / "_state_backups"


def _index_path(backup_root: Path) -> Path:
    return _state_root(backup_root) / "backup_index.json"


def _load_index(backup_root: Path) -> dict[str, Any]:
    path = _index_path(backup_root)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"version": 2, "games": {}}


def _save_index(backup_root: Path, data: dict[str, Any]) -> Path:
    root = _state_root(backup_root)
    root.mkdir(parents=True, exist_ok=True)
    path = _index_path(backup_root)
    data["version"] = 2
    data["updatedAt"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def create_backup_snapshot(files: list[Path], backup_root: Path, label: str = "deploy") -> BackupSnapshot:
    """Create a point-in-time snapshot.

    This remains compatible with old backup_manifest.json files.  It is used for
    "restore to the state before a specific operation".
    """
    backup_root = Path(backup_root)
    snapshot_id = f"{_timestamp()}_{label}"
    snap_dir = backup_root / snapshot_id
    snap_dir.mkdir(parents=True, exist_ok=True)
    entries: list[BackupEntry] = []
    for src in files:
        src = Path(src)
        exists = src.exists()
        if exists:
            dst = snap_dir / _safe_name(src)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        else:
            dst = snap_dir / (src.name + ".missing")
        entries.append(BackupEntry(str(src), str(dst), exists))
    manifest = snap_dir / "backup_manifest.json"
    data = {
        "schema": "fh6-radio-tool-backup-snapshot-v2",
        "snapshotType": "point_in_time",
        "snapshotId": snapshot_id,
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "label": label,
        "entries": [asdict(e) for e in entries],
    }
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return BackupSnapshot(snapshot_id, str(snap_dir), entries, str(manifest))


def ensure_initial_state_snapshot(files: list[Path], backup_root: Path, game_root: Path | None = None, label: str = "initial_game_state") -> BackupSnapshot:
    """Ensure a resource-friendly baseline backup exists for these files.

    The baseline is copied only once per original path for the current game root.
    Later one-click replacements do not duplicate the same original files again.
    This lets users restore to the first state seen by the tool while avoiding a
    full copy of the entire game installation.
    """
    backup_root = Path(backup_root)
    game_root = Path(game_root) if game_root else None
    key = _game_key(game_root)
    state_dir = _state_root(backup_root) / "initial" / key
    state_dir.mkdir(parents=True, exist_ok=True)

    index = _load_index(backup_root)
    games = index.setdefault("games", {})
    game = games.setdefault(key, {
        "gameRoot": str(game_root) if game_root else "",
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "initialEntries": {},
    })
    if game_root and not game.get("gameRoot"):
        game["gameRoot"] = str(game_root)
    entries_map = game.setdefault("initialEntries", {})

    entries: list[BackupEntry] = []
    for src0 in files:
        src = Path(src0)
        src_key = str(src.resolve() if src.exists() else src)
        existing = entries_map.get(src_key)
        exists = src.exists()

        # If a baseline was already captured and the backup file still exists,
        # reuse it.  This prevents repeated one-click runs from consuming space.
        if existing and existing.get("backup") and Path(existing["backup"]).exists():
            entries.append(BackupEntry(str(src), existing["backup"], bool(existing.get("exists", True))))
            continue

        if exists:
            dst = state_dir / _safe_name(src)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            size = src.stat().st_size
            sha256 = _sha256_file(src)
        else:
            dst = state_dir / (src.name + ".missing")
            size = 0
            sha256 = ""

        meta = {
            "original": str(src),
            "backup": str(dst),
            "exists": exists,
            "size": size,
            "sha256": sha256,
            "capturedAt": datetime.now().isoformat(timespec="seconds"),
            "label": label,
        }
        entries_map[src_key] = meta
        entries.append(BackupEntry(str(src), str(dst), exists))

    _save_index(backup_root, index)

    manifest = state_dir / "initial_state_manifest.json"
    data = {
        "schema": "fh6-radio-tool-backup-snapshot-v2",
        "snapshotType": "initial_state",
        "snapshotId": f"initial_{key}",
        "createdAt": game.get("createdAt") or datetime.now().isoformat(timespec="seconds"),
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
        "gameKey": key,
        "gameRoot": game.get("gameRoot", ""),
        "entries": list(entries_map.values()),
        "note": "This is the first state captured by FH6 Radio Tool for each modified file. It stores only touched XML/bank files, not the whole game folder.",
    }
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return BackupSnapshot(f"initial_{key}", str(state_dir), entries, str(manifest))


def restore_snapshot(manifest_path: Path) -> list[str]:
    """Restore a point-in-time or initial-state manifest."""
    manifest_path = Path(manifest_path)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    restored: list[str] = []
    for item in data.get("entries", []):
        if not item.get("exists", True):
            continue
        original = Path(item["original"])
        backup = Path(item["backup"])
        if not backup.exists():
            raise FileNotFoundError(f"备份文件不存在: {backup}")
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, original)
        restored.append(str(original))
    return restored


def restore_initial_state(backup_root: Path, game_root: Path | None = None) -> list[str]:
    """Restore the baseline state for the current game root.

    If game_root is not provided and only one baseline exists, restore that one;
    otherwise ask the caller to provide the game root explicitly.
    """
    backup_root = Path(backup_root)
    index = _load_index(backup_root)
    games = index.get("games", {}) or {}
    if not games:
        raise FileNotFoundError("没有找到初始状态备份。请先执行一次备份或一键替换，让工具捕获初始状态。")

    if game_root:
        key = _game_key(Path(game_root))
        game = games.get(key)
        if not game:
            raise FileNotFoundError("没有找到当前游戏目录对应的初始状态备份。")
    else:
        if len(games) != 1:
            raise RuntimeError("存在多个游戏目录的初始状态备份，请先选择游戏根目录后再恢复。")
        key, game = next(iter(games.items()))

    entries = list((game or {}).get("initialEntries", {}).values())
    if not entries:
        raise FileNotFoundError("初始状态备份为空。")

    restored: list[str] = []
    for item in entries:
        if not item.get("exists", True):
            continue
        original = Path(item["original"])
        backup = Path(item["backup"])
        if not backup.exists():
            raise FileNotFoundError(f"初始状态备份文件不存在: {backup}")
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, original)
        restored.append(str(original))
    return restored
