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
        start = get("TrackStart", 0)
        end = min(total_last, start + seconds * sr)
        return ScenePreviewPlan(scenario, start, max(start + 1, end), False, None, "漫游模式：普通播放")
    if scenario == "race_start":
        ls = get("TrackLoopStart", get("TrackStart", 0))
        le = get("TrackLoopEnd", min(total_last, ls + seconds * sr))
        start = get("TrackDrop", get("TrackStart", 0))
        return ScenePreviewPlan(scenario, start, le, True, ls, "比赛开始：TrackDrop/TrackStart 进入 TrackLoop")
    if scenario == "race_loop":
        ls = get("TrackLoopStart", 0)
        le = get("TrackLoopEnd", total_last)
        start = max(ls, le - seconds * sr)
        return ScenePreviewPlan(scenario, start, le, True, ls, "比赛进行：TrackLoopEnd 前衔接试听")
    if scenario == "finish":
        tle = get("TrackLoopEnd", total_last)
        raw_pd = int(markers.get("PostDrop", -1))
        if 0 <= raw_pd <= total_last:
            start = _clamp(raw_pd, total_last)
            end = min(total_last, start + seconds * sr)
            if end <= start:
                end = min(total_last, start + seconds * sr)
            return ScenePreviewPlan(scenario, start, max(start + 1, end), False, None, "冲线：从 PostDrop 开始")
        start = max(0, tle - seconds * sr)
        end = min(total_last, tle + seconds * sr)
        return ScenePreviewPlan(scenario, start, max(start + 1, end), False, None, "冲线：TrackLoopEnd 前后预览")
    ps = get("PostRaceLoopStart", get("PostDrop", get("TrackLoopStart", 0)))
    pe = get("PostRaceLoopEnd", total_last)
    start = max(ps, pe - seconds * sr)
    return ScenePreviewPlan(scenario, start, pe, True, ps, "冲线后：PostRaceLoopEnd 前衔接试听")
