from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

from ..wav_tools import read_wav_info


@dataclass(frozen=True)
class ABSplicePlan:
    intro_path: str
    loop_path: str
    output_path: str
    splice_sample: int
    loop_start: int
    loop_end: int
    notes: str

    def to_json(self) -> dict:
        return asdict(self)


def build_ab_splice_plan(intro_path: Path, loop_path: Path, output_path: Path) -> ABSplicePlan:
    """Create a logical A/B splice plan for Intro + Loop workflows.

    v2.2 records the plan and marker math; actual rendering is intentionally
    left to the existing WAV normalization/export path so users do not lose the
    original extracted Fmod file layout.
    """
    intro = read_wav_info(Path(intro_path))
    loop = read_wav_info(Path(loop_path))
    if intro.samplerate != loop.samplerate:
        raise ValueError("Intro 与 Loop 的采样率不同，不能直接逻辑拼接。")
    if intro.channels != loop.channels or intro.bits_per_sample != loop.bits_per_sample:
        raise ValueError("Intro 与 Loop 的声道数或位深不同，不能直接逻辑拼接。")
    splice = intro.sample_length
    return ABSplicePlan(
        intro_path=str(intro_path),
        loop_path=str(loop_path),
        output_path=str(output_path),
        splice_sample=splice,
        loop_start=splice,
        loop_end=max(splice + loop.sample_length - 1, splice),
        notes="Intro + Loop logical splice. Render through FH6 output workspace before rebuilding bank.",
    )
