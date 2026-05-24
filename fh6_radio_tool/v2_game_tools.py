from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

from .xml_tools import list_station_infos, parse_xml


@dataclass(frozen=True)
class GameScanResult:
    game_root: Path
    xml_candidates: list[Path]
    selected_xml: Path | None
    bank_candidates: list[Path]
    bank_root: Path | None
    station_count: int
    warnings: list[str]

    def to_json(self) -> dict:
        data = asdict(self)
        data["game_root"] = str(self.game_root)
        data["xml_candidates"] = [str(p) for p in self.xml_candidates]
        data["selected_xml"] = str(self.selected_xml) if self.selected_xml else None
        data["bank_candidates"] = [str(p) for p in self.bank_candidates]
        data["bank_root"] = str(self.bank_root) if self.bank_root else None
        return data


def find_radio_xmls(game_root: Path) -> list[Path]:
    root = Path(game_root)
    if root.is_file() and root.suffix.lower() == ".xml":
        return [root]
    patterns = ["RadioInfo*.xml", "*Radio*.xml"]
    found: list[Path] = []
    for pat in patterns:
        for p in root.rglob(pat):
            if p.is_file() and p not in found:
                found.append(p)
    def score(p: Path):
        name = p.name.lower()
        s = 0
        if name.startswith("radioinfo"):
            s -= 20
        if "cn" in name:
            s -= 5
        return (s, len(str(p)), str(p).lower())
    return sorted(found, key=score)


def find_bank_files(game_root: Path) -> list[Path]:
    root = Path(game_root)
    if root.is_file() and root.suffix.lower() == ".bank":
        return [root]
    banks = [p for p in root.rglob("*.bank") if p.is_file()]
    def score(p: Path):
        name = p.name.lower()
        s = 0
        if "track" in name:
            s -= 20
        if "radio" in name:
            s -= 10
        if "master" in name or "strings" in name:
            s += 50
        return (s, len(str(p)), str(p).lower())
    return sorted(banks, key=score)




COMMON_FMODBANKS_RELATIVE_PATHS = (
    Path("media") / "Audio" / "FMODBanks",
    Path("Media") / "Audio" / "FMODBanks",
    Path("media") / "audio" / "fmodbanks",
    Path("meida") / "Audio" / "FMODBanks",  # tolerate common typo in user docs
)


def find_fmod_bank_roots(game_root: Path) -> list[Path]:
    """Return likely FH6 FMODBanks directories under the game root.

    The user should not have to configure a separate bank root.  We first try
    the canonical game layout `<game>/media/Audio/FMODBanks`, then fall back to
    scanning for directories named FMODBanks or directories that contain bank
    files.  Results are ordered so the canonical FMODBanks path wins.
    """
    root = Path(game_root)
    if root.is_file():
        root = root.parent
    if root.name.lower() == "fmodbanks":
        return [root]

    found: list[Path] = []
    for rel in COMMON_FMODBANKS_RELATIVE_PATHS:
        c = root / rel
        if c.exists() and c.is_dir() and c not in found:
            found.append(c)

    # rglob on a full game directory is acceptable here because scan_game_root
    # already performs recursive discovery; keep this narrow by directory name.
    try:
        for p in root.rglob("*"):
            if p.is_dir() and p.name.lower() == "fmodbanks" and p not in found:
                found.append(p)
    except OSError:
        pass

    # Last resort: use the common parent of discovered bank files.
    banks = find_bank_files(root)
    for b in banks[:64]:
        parent = b.parent
        if parent not in found:
            found.append(parent)

    def score(p: Path):
        parts = [x.lower() for x in p.parts]
        joined = "/".join(parts)
        s = 0
        if p.name.lower() == "fmodbanks":
            s -= 50
        if "media/audio/fmodbanks" in joined or "media\\audio\\fmodbanks" in joined:
            s -= 60
        if "backup" in joined or "output" in joined or "work" in joined:
            s += 40
        # Prefer dirs that actually contain bank files, but do not recurse too deeply.
        try:
            direct_banks = len(list(p.glob("*.bank")))
        except OSError:
            direct_banks = 0
        s -= min(20, direct_banks)
        return (s, len(str(p)), str(p).lower())

    return sorted(found, key=score)


def resolve_fmod_bank_root(game_root: Path, *, bank_names: list[str] | None = None) -> Path | None:
    """Pick the best FMODBanks directory, optionally requiring station bank names."""
    roots = find_fmod_bank_roots(game_root)
    if not roots:
        return None
    names = [n for n in (bank_names or []) if n]
    if names:
        wanted = {f"{n}.bank".lower() for n in names}
        best: tuple[int, Path] | None = None
        for root in roots:
            try:
                available = {p.name.lower() for p in root.rglob("*.bank")}
            except OSError:
                available = set()
            hits = len(wanted & available)
            if best is None or hits > best[0]:
                best = (hits, root)
        if best and best[0] > 0:
            return best[1]
    return roots[0]


def scan_game_root(game_root: Path) -> GameScanResult:
    root = Path(game_root)
    warnings: list[str] = []
    if not root.exists():
        raise FileNotFoundError(f"游戏根目录不存在: {root}")
    xmls = find_radio_xmls(root)
    banks = find_bank_files(root)
    selected = xmls[0] if xmls else None
    station_count = 0
    station_bank_names: list[str] = []
    if selected:
        try:
            stations = list_station_infos(parse_xml(selected))
            station_count = len(stations)
            for st in stations:
                station_bank_names.extend(st.banks)
        except Exception as exc:
            warnings.append(f"XML 可疑但无法解析电台: {selected}: {exc}")
    else:
        warnings.append("没有找到 RadioInfo*.xml。可手动选择 XML。")
    bank_root = resolve_fmod_bank_root(root, bank_names=station_bank_names)
    if not banks:
        warnings.append("没有找到 .bank 文件。Extract/Rebuild 需要检查游戏根目录是否正确。")
    if not bank_root:
        warnings.append("没有自动定位到 media/Audio/FMODBanks。请确认游戏根目录是否选到了 FH6 安装根目录。")
    return GameScanResult(root, xmls, selected, banks, bank_root, station_count, warnings)


def write_scan_report(result: GameScanResult, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
