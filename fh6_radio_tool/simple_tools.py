from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .runtime_tools import runtime_root

from .core import PrepareOptions, prepare_dry_run
from .xml_tools import list_station_infos, parse_xml

try:
    from .bank_tools import DEFAULT_COMMAND_TEMPLATE, make_bank_plan, write_bank_plan_outputs
except Exception:
    DEFAULT_COMMAND_TEMPLATE = '"{tool}" --bank "{bank_path}" --wav-dir "{music_dir}" --list "{bank_txt_path}" --out "{out_bank_path}"'
    make_bank_plan = None
    write_bank_plan_outputs = None


DEFAULT_OUT_DIR_NAME = "_fh6_radio_output"
TOOLS_DIR = "tools"
FMOD_TOOLS_DIR = "FmodBankTools"

FMOD_EXE_NAMES = [
    "Fmod Bank Tools.exe",
    "FmodBankTools.exe",
    "FmodBankToolsCLI.exe",
    "fmod_bank_tools.exe",
    "FmodBankTool.exe",
    "banktools.exe",
    "banktool.exe",
]


@dataclass(frozen=True)
class StationInferResult:
    station_name: str
    reason: str
    warnings: list[str]


@dataclass(frozen=True)
class ToolDiscoveryResult:
    fmod_tool_path: Path | None
    config_path: Path
    command_template: str
    warnings: list[str]


@dataclass(frozen=True)
class BankRootInferResult:
    bank_root: Path | None
    reason: str
    warnings: list[str]


def app_root() -> Path:
    return runtime_root()


def default_output_dir(music_dir: Path) -> Path:
    return Path(music_dir) / DEFAULT_OUT_DIR_NAME


def _tokens(text: str) -> list[str]:
    parts = re.split(r"[^0-9a-zA-Z\u4e00-\u9fff]+", text.lower())
    stop = {"horizon", "radio", "station", "music", "fh6", "forza"}
    return [p for p in parts if p and p not in stop]


def infer_station_name(xml_path: Path, music_dir: Path) -> StationInferResult:
    tree = parse_xml(xml_path)
    infos = list_station_infos(tree)
    if not infos:
        raise ValueError("XML 中没有识别到任何电台。")

    names = [x.name for x in infos]
    folder_tokens = set(_tokens(music_dir.name)) | set(_tokens(music_dir.parent.name))

    scored: list[tuple[int, str, list[str]]] = []
    for name in names:
        name_tokens = set(_tokens(name))
        overlap = sorted(name_tokens & folder_tokens)
        score = len(overlap)
        normalized_folder = "".join(_tokens(music_dir.name))
        normalized_name = "".join(_tokens(name))
        if normalized_folder and normalized_folder in normalized_name:
            score += 1
        if normalized_name and normalized_name in normalized_folder:
            score += 2
        scored.append((score, name, overlap))

    scored.sort(key=lambda x: (-x[0], names.index(x[1])))

    if scored and scored[0][0] > 0:
        _, name, overlap = scored[0]
        return StationInferResult(
            station_name=name,
            reason=f"根据音乐文件夹名自动匹配：{', '.join(overlap) if overlap else music_dir.name}",
            warnings=[],
        )

    return StationInferResult(
        station_name=infos[0].name,
        reason="未能根据音乐文件夹名匹配，默认选择 XML 中第一个电台",
        warnings=[f"未能从音乐文件夹名“{music_dir.name}”匹配电台，已默认选择：{infos[0].name}。"],
    )


def load_fmod_config(config_path: Path) -> dict:
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({
            "command_template": DEFAULT_COMMAND_TEMPLATE,
            "note": "开发者可在这里配置 Fmod Bank Tools 的实际命令行模板。普通用户无需修改。"
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return data


def discover_fmod_tool() -> ToolDiscoveryResult:
    root = app_root()
    tool_dir = root / TOOLS_DIR / FMOD_TOOLS_DIR
    tool_dir.mkdir(parents=True, exist_ok=True)

    placeholder = tool_dir / "PUT_FMOD_BANK_TOOLS_HERE.txt"
    if not placeholder.exists():
        placeholder.write_text(
            "请把教程中的 Fmod Bank Tools 可执行文件放在本目录下。普通用户无需了解具体路径。\n",
            encoding="utf-8",
        )

    config_path = tool_dir / "fmod_bank_tools_config.json"
    config = load_fmod_config(config_path)
    command_template = str(config.get("command_template") or DEFAULT_COMMAND_TEMPLATE)

    warnings: list[str] = []
    configured = config.get("tool_exe")
    candidates: list[Path] = []
    if configured:
        candidates.append(tool_dir / str(configured))

    for name in FMOD_EXE_NAMES:
        candidates.append(tool_dir / name)

    for p in candidates:
        if p.exists() and p.is_file():
            return ToolDiscoveryResult(
                fmod_tool_path=p,
                config_path=config_path,
                command_template=command_template,
                warnings=warnings,
            )

    for p in tool_dir.glob("*.exe"):
        return ToolDiscoveryResult(
            fmod_tool_path=p,
            config_path=config_path,
            command_template=command_template,
            warnings=warnings,
        )

    warnings.append(f"未在 {tool_dir} 找到 Fmod Bank Tools，可先生成 XML 和音频清单，bank 重构稍后再做。")
    return ToolDiscoveryResult(
        fmod_tool_path=None,
        config_path=config_path,
        command_template=command_template,
        warnings=warnings,
    )


def infer_bank_root(xml_path: Path) -> BankRootInferResult:
    base = Path(xml_path).parent
    candidates = [
        base,
        base / "Banks",
        base / "banks",
        base.parent,
        base.parent / "Banks",
        base.parent / "banks",
        base.parent / "Audio",
        base.parent / "Audio" / "Banks",
    ]

    seen: set[Path] = set()
    unique: list[Path] = []
    for c in candidates:
        try:
            key = c.resolve()
        except Exception:
            key = c
        if key not in seen:
            seen.add(key)
            unique.append(c)

    for c in unique:
        if c.exists() and any(c.rglob("*.bank")):
            return BankRootInferResult(bank_root=c, reason=f"从 XML 附近自动发现 .bank 文件：{c}", warnings=[])

    return BankRootInferResult(
        bank_root=None,
        reason="未能从 XML 所在目录附近自动发现 .bank 文件",
        warnings=["未能自动发现 bank 根目录，因此本次只生成音频清单和 patched XML。"],
    )


def write_summary(out_dir: Path, result: dict, station_infer: StationInferResult, tool_result: ToolDiscoveryResult, bank_result: BankRootInferResult, bank_plan_paths: dict | None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "summary.txt"

    lines = []
    lines.append("FH6 Radio Tool 处理结果")
    lines.append("=" * 36)
    lines.append(f"固定输出目录: {out_dir}")
    lines.append(f"目标电台: {result['station'].name}")
    lines.append(f"电台选择: {station_infer.reason}")

    all_warnings = []
    all_warnings.extend(station_infer.warnings)
    all_warnings.extend(tool_result.warnings)
    all_warnings.extend(bank_result.warnings)

    if all_warnings:
        lines.append("")
        lines.append("提示/警告:")
        for w in all_warnings:
            lines.append(f"  - {w}")

    lines.append("")
    lines.append("音频处理:")
    lines.append(f"  候选音频: {result['candidate_count']}")
    lines.append(f"  使用音频: {result['used_count']}")
    lines.append(f"  原地使用合规音频: {result['original_count']}")
    lines.append(f"  FFmpeg 规范化音频: {result['normalized_count']}")
    lines.append(f"  丢弃音频: {result['discarded_count']}")
    if result["discarded_files"]:
        lines.append("  被丢弃文件:")
        for item in result["discarded_files"]:
            lines.append(f"    - {item}")

    lines.append("")
    lines.append("实际采用文件:")
    for item in result["adopted_files"]:
        lines.append(f"  - {item}")

    lines.append("")
    lines.append("生成文件:")
    lines.append(f"  bank_wav_list.txt: {result['bank_txt_path']}")
    lines.append(f"  patched XML: {result['patched_xml_path']}")
    lines.append(f"  manifest: {result['manifest_path']}")
    lines.append(f"  audio report: {result['audio_report_path']}")
    lines.append(f"  segments: {result['segments_path']}")

    lines.append("")
    lines.append("Fmod Bank Tools:")
    lines.append(f"  工具路径: {tool_result.fmod_tool_path if tool_result.fmod_tool_path else '<未找到>'}")
    lines.append(f"  配置文件: {tool_result.config_path}")
    lines.append(f"  bank 根目录: {bank_result.bank_root if bank_result.bank_root else '<未找到>'}")
    lines.append(f"  bank 根目录推断: {bank_result.reason}")

    if bank_plan_paths:
        lines.append("")
        lines.append("bank 接入预览:")
        for k, v in bank_plan_paths.items():
            lines.append(f"  {k}: {v}")

    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def run_unified(xml_path: Path, music_dir: Path) -> dict:
    xml_path = Path(xml_path)
    music_dir = Path(music_dir)
    out_dir = default_output_dir(music_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    station_infer = infer_station_name(xml_path, music_dir)

    prepare_options = PrepareOptions(
        xml_path=xml_path,
        station_name=station_infer.station_name,
        music_dir=music_dir,
        out_dir=out_dir,
        auto_normalize=True,
        ffmpeg_path=None,
    )
    result = prepare_dry_run(prepare_options)

    tool_result = discover_fmod_tool()
    bank_result = infer_bank_root(xml_path)

    bank_plan_paths = None
    if make_bank_plan and write_bank_plan_outputs and bank_result.bank_root:
        try:
            plan = make_bank_plan(
                xml_path=xml_path,
                station_name=station_infer.station_name,
                bank_root=bank_result.bank_root,
                music_dir=music_dir,
                out_dir=out_dir,
                tool_path=tool_result.fmod_tool_path,
                command_template=tool_result.command_template,
                bank_filter="Tracks",
            )
            json_path, txt_path, bat_path = write_bank_plan_outputs(plan, out_dir)
            bank_plan_paths = {
                "bank_plan.json": json_path,
                "bank_plan.txt": txt_path,
                "bank_commands_preview.bat": bat_path,
            }
        except Exception as exc:
            bank_plan_paths = {"bank_plan_error": str(exc)}

    summary_path = write_summary(out_dir, result, station_infer, tool_result, bank_result, bank_plan_paths)

    result["fixed_out_dir"] = out_dir
    result["summary_path"] = summary_path
    result["station_infer"] = station_infer
    result["tool_discovery"] = tool_result
    result["bank_root_infer"] = bank_result
    result["bank_plan_paths"] = bank_plan_paths
    return result


def result_to_text(result: dict) -> str:
    lines = []
    lines.append("[OK] 处理完成")
    lines.append(f"  固定输出目录: {result['fixed_out_dir']}")
    lines.append(f"  目标电台: {result['station'].name}")
    lines.append(f"  电台选择: {result['station_infer'].reason}")
    lines.append(f"  使用音频: {result['used_count']}")
    lines.append(f"  原地使用合规音频: {result['original_count']}")
    lines.append(f"  FFmpeg规范化音频: {result['normalized_count']}")
    lines.append(f"  丢弃音频: {result['discarded_count']}")
    lines.append(f"  TXT清单: {result['bank_txt_path']}")
    lines.append(f"  patched XML: {result['patched_xml_path']}")
    lines.append(f"  摘要: {result['summary_path']}")
    if result.get("bank_plan_paths"):
        lines.append("  bank 接入预览已生成。")
    return "\n".join(lines)
