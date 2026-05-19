from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class ScenePreviewPlan:
    scenario: str
    start_sample: int
    end_sample: int
    loop: bool
    loop_start_sample: int | None
    description: str

    def to_json(self) -> dict:
        return asdict(self)


def _clamp(v: int, total_last: int) -> int:
    return max(0, min(int(v), max(0, int(total_last))))


def build_scene_preview_plan(scenario: str, markers: dict[str, int], total_frames: int, samplerate: int, preview_seconds: int = 5) -> ScenePreviewPlan:
    total_last = max(0, int(total_frames) - 1)
    sr = max(1, int(samplerate))
    seconds = max(1, int(preview_seconds))

    def get(name: str, fallback: int) -> int:
        return _clamp(markers.get(name, fallback), total_last)

    if scenario == "roam_loop":
        ls = get("TrackLoopStart", get("TrackStart", 0))
        le = get("TrackLoopEnd", total_last)
        start = max(ls, le - seconds * sr)
        return ScenePreviewPlan(scenario, start, le, True, ls, "漫游模式：TrackLoop 循环")
    if scenario == "race_start":
        ls = get("TrackLoopStart", get("TrackStart", 0))
        le = get("TrackLoopEnd", min(total_last, ls + seconds * sr))
        start = get("TrackDrop", get("TrackStart", 0))
        return ScenePreviewPlan(scenario, start, le, True, ls, "比赛开始：TrackDrop/TrackStart 进入 TrackLoop")
    if scenario == "race_loop":
        ls = get("TrackLoopStart", 0)
        le = get("TrackLoopEnd", total_last)
        return ScenePreviewPlan(scenario, ls, le, True, ls, "比赛进行：TrackLoop 循环")
    if scenario == "finish":
        tle = get("TrackLoopEnd", total_last)
        pd = get("PostDrop", min(total_last, tle + seconds * sr))
        start = max(0, tle - seconds * sr)
        end = max(start + 1, min(total_last, pd + seconds * sr))
        return ScenePreviewPlan(scenario, start, end, False, None, "冲线：TrackLoopEnd 前后到 PostDrop 附近")
    ps = get("PostRaceLoopStart", get("PostDrop", get("TrackLoopStart", 0)))
    pe = get("PostRaceLoopEnd", total_last)
    return ScenePreviewPlan(scenario, ps, pe, True, ps, "冲线后：PostRaceLoop 循环")
