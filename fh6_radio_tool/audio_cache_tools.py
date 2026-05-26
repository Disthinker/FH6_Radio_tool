from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

MIN_USER_GAIN_DB = -6.0
MAX_USER_GAIN_DB = 6.0


def normalize_user_gain_db(value: Any) -> float:
    try:
        gain = float(value)
    except Exception:
        gain = 0.0
    gain = max(MIN_USER_GAIN_DB, min(MAX_USER_GAIN_DB, gain))
    return round(gain, 2)


def source_audio_signature(source: Path) -> dict[str, Any]:
    """Return stable metadata for prepared-audio cache validation."""
    source = Path(source)
    try:
        stat = source.stat()
        resolved = str(source.resolve())
        size = int(stat.st_size)
        mtime_ns = int(stat.st_mtime_ns)
        payload = f"{resolved}|{size}|{mtime_ns}"
    except Exception:
        resolved = str(source)
        size = None
        mtime_ns = None
        payload = resolved
    return {
        "source_path_resolved": resolved,
        "source_size": size,
        "source_mtime_ns": mtime_ns,
        "source_cache_key": hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:16],
    }


def prepared_audio_cache_key(source: Path, user_gain_db: Any = 0.0) -> str:
    signature = source_audio_signature(source)
    source_key = str(signature.get("source_cache_key") or "")
    gain = normalize_user_gain_db(user_gain_db)
    if abs(gain) < 0.005:
        return source_key
    payload = f"{source_key}|user_gain_db={gain:.2f}"
    return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:16]


def loudness_user_gain_db(loudness: dict[str, Any] | None) -> float:
    if not isinstance(loudness, dict):
        return 0.0
    return normalize_user_gain_db(loudness.get("user_gain_db", 0.0))


def prepared_cache_matches_gain(loudness: dict[str, Any] | None, user_gain_db: Any = 0.0) -> bool:
    return abs(loudness_user_gain_db(loudness) - normalize_user_gain_db(user_gain_db)) < 0.005


def prepared_cache_matches_source(loudness: dict[str, Any] | None, source: Path) -> bool:
    """Check whether a profile's prepared WAV metadata belongs to source."""
    if not isinstance(loudness, dict) or not loudness:
        return False
    try:
        current = source_audio_signature(source)
    except Exception:
        return False

    stored_key = loudness.get("source_cache_key")
    if stored_key:
        return str(stored_key) == str(current.get("source_cache_key"))

    stored_path = loudness.get("source_path_resolved")
    stored_size = loudness.get("source_size")
    stored_mtime_ns = loudness.get("source_mtime_ns")
    if not stored_path or stored_size is None or stored_mtime_ns is None:
        return False
    return (
        str(stored_path).casefold() == str(current.get("source_path_resolved")).casefold()
        and int(stored_size) == int(current.get("source_size") or -1)
        and int(stored_mtime_ns) == int(current.get("source_mtime_ns") or -1)
    )
