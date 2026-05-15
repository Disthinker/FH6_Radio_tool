from __future__ import annotations

import json
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path

from .txt_tools import read_bank_wav_list
from .xml_tools import find_station, parse_xml, station_info_from_node


@dataclass(frozen=True)
class BankCandidate:
    name: str
    bank_path: Path | None
    exists: bool
    selected: bool
    reason: str


@dataclass(frozen=True)
class BankAudioCheck:
    filename: str
    path: Path
    exists: bool


@dataclass(frozen=True)
class BankPlan:
    station_name: str
    station_number: str | None
    bank_root: Path
    music_dir: Path
    bank_txt_path: Path
    tool_path: Path | None
    out_dir: Path
    command_template: str
    bank_candidates: list[BankCandidate]
    audio_checks: list[BankAudioCheck]
    commands: list[str]
    warnings: list[str]
    errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


DEFAULT_COMMAND_TEMPLATE = (
    '"{tool}" '
    '--bank "{bank_path}" '
    '--wav-dir "{music_dir}" '
    '--list "{bank_txt_path}" '
    '--out "{out_bank_path}"'
)


def shell_quote_win(text: str) -> str:
    # 生成 .bat 预览时保持可读性；路径统一加双引号。
    escaped = text.replace('"', '\"')
    return f'"{escaped}"'


def is_track_bank_name(name: str) -> bool:
    lower = name.lower()
    if "track" not in lower:
        return False
    # 轻量默认策略：只选择 Track bank，不选 Stinger / VO / DJ。
    if "stinger" in lower or lower.startswith("vo_") or "_vo_" in lower or "dj" in lower:
        return False
    return True


def resolve_bank_file(bank_root: Path, bank_name: str) -> Path | None:
    direct = bank_root / f"{bank_name}.bank"
    if direct.exists():
        return direct

    # 宽松查找：大小写不同或后缀不同布局。
    matches = list(bank_root.rglob(f"{bank_name}.bank"))
    if matches:
        return matches[0]

    lower_name = f"{bank_name}.bank".lower()
    for p in bank_root.rglob("*.bank"):
        if p.name.lower() == lower_name:
            return p

    return direct


def choose_banks(
    bank_names: list[str],
    bank_root: Path,
    include_all_banks: bool = False,
    bank_filter: str | None = None,
) -> list[BankCandidate]:
    candidates: list[BankCandidate] = []
    bank_filter_lower = bank_filter.lower() if bank_filter else None

    for name in bank_names:
        if include_all_banks:
            selected = True
            reason = "include_all_banks"
        elif bank_filter_lower:
            selected = bank_filter_lower in name.lower()
            reason = f"filter contains '{bank_filter}'" if selected else f"filter excludes '{bank_filter}'"
        else:
            selected = is_track_bank_name(name)
            reason = "auto track-bank filter" if selected else "excluded by default non-track filter"

        path = resolve_bank_file(bank_root, name)
        exists = bool(path and path.exists())
        candidates.append(
            BankCandidate(
                name=name,
                bank_path=path,
                exists=exists,
                selected=selected,
                reason=reason,
            )
        )

    return candidates


def build_command(
    template: str,
    tool_path: Path | None,
    bank_name: str,
    bank_path: Path,
    music_dir: Path,
    bank_txt_path: Path,
    out_dir: Path,
    station_name: str,
) -> str:
    out_bank_path = out_dir / f"{bank_name}.bank"

    values = {
        "tool": str(tool_path) if tool_path else "<BANK_TOOL_EXE>",
        "bank": bank_name,
        "bank_path": str(bank_path),
        "music_dir": str(music_dir),
        "bank_txt_path": str(bank_txt_path),
        "txt": str(bank_txt_path),
        "out_dir": str(out_dir),
        "out_bank_path": str(out_bank_path),
        "station": station_name,
    }
    return template.format(**values)


def make_bank_plan(
    xml_path: Path,
    station_name: str,
    bank_root: Path,
    music_dir: Path,
    out_dir: Path,
    bank_txt_path: Path | None = None,
    tool_path: Path | None = None,
    command_template: str = DEFAULT_COMMAND_TEMPLATE,
    include_all_banks: bool = False,
    bank_filter: str | None = None,
) -> BankPlan:
    tree = parse_xml(xml_path)
    station = station_info_from_node(find_station(tree, station_name))

    bank_root = Path(bank_root)
    music_dir = Path(music_dir)
    out_dir = Path(out_dir)
    bank_txt_path = Path(bank_txt_path) if bank_txt_path else (music_dir / "bank_wav_list.txt")
    tool_path = Path(tool_path) if tool_path else None

    warnings: list[str] = []
    errors: list[str] = []

    if not bank_root.exists():
        errors.append(f"bank 根目录不存在: {bank_root}")
    if not music_dir.exists():
        errors.append(f"音乐目录不存在: {music_dir}")
    if not bank_txt_path.exists():
        errors.append(f"bank txt 清单不存在: {bank_txt_path}")
    if tool_path and not tool_path.exists():
        warnings.append(f"外部 bank 工具不存在: {tool_path}")

    bank_candidates = choose_banks(
        station.banks,
        bank_root=bank_root,
        include_all_banks=include_all_banks,
        bank_filter=bank_filter,
    )

    selected_banks = [b for b in bank_candidates if b.selected]
    if not selected_banks:
        errors.append("没有选中任何 bank。可尝试 --include-all-banks 或 --bank-filter Tracks。")

    for b in selected_banks:
        if not b.exists:
            warnings.append(f"选中的 bank 文件不存在: {b.name} -> {b.bank_path}")

    audio_checks: list[BankAudioCheck] = []
    if bank_txt_path.exists():
        try:
            wav_names = read_bank_wav_list(bank_txt_path)
            if not wav_names:
                errors.append(f"bank txt 清单为空: {bank_txt_path}")
            for name in wav_names:
                p = music_dir / name
                exists = p.exists()
                audio_checks.append(BankAudioCheck(filename=name, path=p, exists=exists))
                if not exists:
                    errors.append(f"txt 中引用的音频不存在: {name} -> {p}")
        except Exception as exc:
            errors.append(f"读取 bank txt 清单失败: {exc}")

    commands: list[str] = []
    for b in selected_banks:
        if b.bank_path is None:
            continue
        commands.append(
            build_command(
                template=command_template,
                tool_path=tool_path,
                bank_name=b.name,
                bank_path=b.bank_path,
                music_dir=music_dir,
                bank_txt_path=bank_txt_path,
                out_dir=out_dir,
                station_name=station_name,
            )
        )

    return BankPlan(
        station_name=station.name,
        station_number=station.number,
        bank_root=bank_root,
        music_dir=music_dir,
        bank_txt_path=bank_txt_path,
        tool_path=tool_path,
        out_dir=out_dir,
        command_template=command_template,
        bank_candidates=bank_candidates,
        audio_checks=audio_checks,
        commands=commands,
        warnings=warnings,
        errors=errors,
    )


def bank_plan_to_text(plan: BankPlan) -> str:
    lines: list[str] = []
    lines.append("外部 bank 工具接入预览：")
    lines.append(f"  station       : {plan.station_name} / Number={plan.station_number}")
    lines.append(f"  bank root     : {plan.bank_root}")
    lines.append(f"  music dir     : {plan.music_dir}")
    lines.append(f"  bank txt      : {plan.bank_txt_path}")
    lines.append(f"  tool          : {plan.tool_path if plan.tool_path else '<未指定，仅生成命令模板>'}")
    lines.append(f"  out dir       : {plan.out_dir}")
    lines.append("")

    lines.append("Bank 候选：")
    for b in plan.bank_candidates:
        mark = "[SELECT]" if b.selected else "[skip]"
        exists = "exists" if b.exists else "missing"
        lines.append(f"  {mark} {b.name} | {exists} | {b.bank_path} | {b.reason}")

    lines.append("")
    lines.append("音频清单检查：")
    if plan.audio_checks:
        for a in plan.audio_checks:
            exists = "exists" if a.exists else "missing"
            lines.append(f"  - {a.filename} | {exists} | {a.path}")
    else:
        lines.append("  <empty>")

    if plan.warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in plan.warnings:
            lines.append(f"  - {w}")

    if plan.errors:
        lines.append("")
        lines.append("Errors:")
        for e in plan.errors:
            lines.append(f"  - {e}")

    lines.append("")
    lines.append("命令预览：")
    if plan.commands:
        for cmd in plan.commands:
            lines.append(f"  {cmd}")
    else:
        lines.append("  <no commands>")

    lines.append("")
    lines.append("Result:")
    lines.append("  可进入手动 bank 工具验证阶段" if plan.ok else "  存在错误，请先修复")
    return "\n".join(lines)


def write_bank_plan_outputs(plan: BankPlan, out_dir: Path) -> tuple[Path, Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "bank_plan.json"
    txt_path = out_dir / "bank_plan.txt"
    bat_path = out_dir / "bank_commands_preview.bat"

    json_path.write_text(
        json.dumps(asdict(plan), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    txt_path.write_text(bank_plan_to_text(plan), encoding="utf-8")

    lines = [
        "@echo off",
        "REM FH6 Radio Tool - external bank tool command preview",
        "REM This file is generated for manual review.",
        "REM It intentionally does NOT guarantee that the external tool syntax is correct.",
        "",
    ]
    if plan.commands:
        for idx, cmd in enumerate(plan.commands, start=1):
            lines.append(f"echo [BANK {idx}] {cmd}")
            lines.append(f"REM {cmd}")
            lines.append("")
    else:
        lines.append("echo No commands generated.")
    lines.append("pause")
    bat_path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")

    return json_path, txt_path, bat_path
