from __future__ import annotations

import argparse
from pathlib import Path

from .core import InvalidAudioError
from .project_tools import prepare_project_outputs
from .segment_tools import sample_to_seconds, seconds_to_sample
from .simple_tools import infer_station_name
from .xml_tools import list_station_infos, parse_xml


def cmd_scan(args: argparse.Namespace) -> int:
    tree = parse_xml(Path(args.xml))
    for info in list_station_infos(tree):
        print(f"{info.name} | Number={info.number} | TrackSlots={info.track_slot_count} | Banks={','.join(info.banks)}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    try:
        infer = infer_station_name(Path(args.xml), Path(args.music_dir))
        result = prepare_project_outputs(Path(args.xml), Path(args.music_dir), infer.station_name)
    except InvalidAudioError as exc:
        print("[ERROR] 存在无法规范化或不符合要求的音频：")
        for file, errors in exc.invalid_files.items():
            print(f"  - {file}")
            for e in errors:
                print(f"    * {e}")
        return 2

    print("[OK] 已生成精简 output")
    print(f"  电台: {result.station_name}")
    print(f"  XML: {result.output_xml}")
    print(f"  output: {result.output_dir}")
    print("  bank 重构：Wav Output Directory 指向 output/fmod_ready_wav；音量已自动匹配。")
    return 0


def cmd_calc(args: argparse.Namespace) -> int:
    if args.seconds is not None:
        sample = seconds_to_sample(args.seconds, args.samplerate)
        print(f"{args.seconds:.6f} 秒 @ {args.samplerate} Hz = {sample} samples")
        return 0
    if args.sample is not None:
        sec = sample_to_seconds(args.sample, args.samplerate)
        print(f"{args.sample} samples @ {args.samplerate} Hz = {sec:.6f} 秒")
        return 0
    print("请提供 --seconds 或 --sample")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FH6 Radio Tool v3.4")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="扫描 XML 中的电台")
    scan.add_argument("--xml", required=True)
    scan.set_defaults(func=cmd_scan)

    run = sub.add_parser("run", help="生成精简 output 目录")
    run.add_argument("--xml", required=True)
    run.add_argument("--music-dir", required=True)
    run.set_defaults(func=cmd_run)

    calc = sub.add_parser("calc", help="秒数与采样点换算")
    calc.add_argument("--samplerate", type=int, default=48000)
    group = calc.add_mutually_exclusive_group(required=True)
    group.add_argument("--seconds", type=float)
    group.add_argument("--sample", type=int)
    calc.set_defaults(func=cmd_calc)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
