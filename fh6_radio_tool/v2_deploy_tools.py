from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path


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


def create_backup_snapshot(files: list[Path], backup_root: Path, label: str = "deploy") -> BackupSnapshot:
    backup_root = Path(backup_root)
    snapshot_id = f"{_timestamp()}_{label}"
    snap_dir = backup_root / snapshot_id
    snap_dir.mkdir(parents=True, exist_ok=True)
    entries: list[BackupEntry] = []
    for src in files:
        src = Path(src)
        exists = src.exists()
        if exists:
            # Keep drive letters readable on Windows paths.
            safe_name = str(src).replace(":", "").replace("\\", "/").strip("/").replace("/", "__")
            dst = snap_dir / safe_name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        else:
            dst = snap_dir / (src.name + ".missing")
        entries.append(BackupEntry(str(src), str(dst), exists))
    manifest = snap_dir / "backup_manifest.json"
    data = {
        "snapshotId": snapshot_id,
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "entries": [asdict(e) for e in entries],
    }
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return BackupSnapshot(snapshot_id, str(snap_dir), entries, str(manifest))


def restore_snapshot(manifest_path: Path) -> list[str]:
    manifest_path = Path(manifest_path)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    restored: list[str] = []
    for item in data.get("entries", []):
        if not item.get("exists"):
            continue
        original = Path(item["original"])
        backup = Path(item["backup"])
        if not backup.exists():
            raise FileNotFoundError(f"备份文件不存在: {backup}")
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, original)
        restored.append(str(original))
    return restored
